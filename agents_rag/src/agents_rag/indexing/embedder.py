"""智谱 embedding-3 向量化：批量 + 并发限流 + tenacity 重试 + 缓存。

笔记 §6.4（批处理 / 并发 / 重试）/ §6.5（缓存）/ §6.13（缓存键版本化）。

``Embedder`` 基类在 ``embed`` 中统一处理缓存命中、分批、并发；子类只实现
``_embed_batch``（实际 API 调用），便于测试用确定性 Fake 替换。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_rag.indexing.cache import EmbeddingCache

log = logging.getLogger(__name__)

_AUTH_MARKERS = ("401", "403", "invalid api key", "authentication", "unauthorized")


class _NonRetryable(Exception):
    """不可重试错误（鉴权 / 参数错误）。"""


def _is_non_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(m in msg for m in _AUTH_MARKERS)


class Embedder(ABC):
    """向量化抽象。"""

    max_batch: int = 64
    max_concurrency: int = 1

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @abstractmethod
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """对一批文本（≤ max_batch）调用一次 API，返回向量列表。"""

    def embed(
        self, texts: list[str], cache: EmbeddingCache | None = None
    ) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        pending: list[int] = []
        if cache is not None:
            for i, t in enumerate(texts):
                v = cache.get(t, self.model, self.dim)
                if v is not None:
                    results[i] = v
                else:
                    pending.append(i)
        else:
            pending = list(range(len(texts)))

        if not pending:
            return results  # type: ignore[return-value]

        batches = [
            pending[s : s + self.max_batch]
            for s in range(0, len(pending), self.max_batch)
        ]
        workers = max(1, self.max_concurrency)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            # ex.map 按提交顺序返回；在主线程写缓存，避免 sqlite 多线程写
            for batch_idx, vecs in ex.map(
                lambda b: (b, self._embed_batch([texts[i] for i in b])), batches
            ):
                for i, v in zip(batch_idx, vecs):
                    results[i] = v
                    if cache is not None:
                        cache.put(texts[i], self.model, self.dim, v)
        return results  # type: ignore[return-value]


class ZhipuEmbedder(Embedder):
    """智谱 embedding-3 实现。"""

    def __init__(
        self,
        api_key: str,
        model: str = "embedding-3",
        dim: int = 2048,
        max_batch: int = 64,
        max_concurrency: int = 8,
        retry_stop=None,
        retry_wait=None,
    ):
        from zhipuai import ZhipuAI

        self._client = ZhipuAI(api_key=api_key)
        self._model = model
        self._dim = dim
        self.max_batch = max_batch
        self.max_concurrency = max_concurrency
        self._retry_stop = retry_stop or stop_after_attempt(5)
        self._retry_wait = retry_wait or wait_exponential(min=1, max=10)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        @retry(
            stop=self._retry_stop,
            wait=self._retry_wait,
            retry=retry_if_not_exception_type(_NonRetryable),
            reraise=True,
        )
        def _do() -> list[list[float]]:
            try:
                resp = self._client.embeddings.create(
                    model=self._model, input=texts, dimensions=self._dim
                )
                return [d.embedding for d in resp.data]
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise  # 可重试（429 / 5xx / 网络）

        return _do()

"""Rerank 精排：智谱 Rerank API。笔记 §4。

cross-encoder 精排 RRF 融合后的候选，取 top_n。
智谱 rerank 走 HTTP POST /rerank（OpenAI 兼容端点），返回 index + relevance_score。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_rag.indexing.embedder import _NonRetryable, _is_non_retryable
from agents_rag.models import RetrievalResult

log = logging.getLogger(__name__)


class Reranker(ABC):
    @abstractmethod
    def rerank(
        self, query: str, candidates: list[RetrievalResult], top_n: int = 6
    ) -> list[RetrievalResult]:
        raise NotImplementedError


class ZhipuReranker(Reranker):
    """智谱 Rerank API（HTTP /rerank 端点）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "rerank-2",
        retry_stop=None,
        retry_wait=None,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._retry_stop = retry_stop or stop_after_attempt(4)
        self._retry_wait = retry_wait or wait_exponential(min=1, max=10)

    def rerank(
        self, query: str, candidates: list[RetrievalResult], top_n: int = 6
    ) -> list[RetrievalResult]:
        if not candidates:
            return []

        @retry(
            stop=self._retry_stop,
            wait=self._retry_wait,
            retry=retry_if_not_exception_type(_NonRetryable),
            reraise=True,
        )
        def _do() -> list[RetrievalResult]:
            try:
                resp = httpx.post(
                    f"{self._base_url}/rerank",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "query": query,
                        "documents": [c.text for c in candidates],
                        "top_n": min(top_n, len(candidates)),
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                ranked: list[RetrievalResult] = []
                for r in results:
                    idx = r["index"]
                    score = r.get("relevance_score", 0.0)
                    ranked.append(
                        candidates[idx].model_copy(
                            update={"score": score, "retriever": "reranked"}
                        )
                    )
                return ranked
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise

        try:
            return _do()
        except Exception as e:  # noqa: BLE001
            log.warning("Rerank 失败，使用原序: %s", e)
            return [c.model_copy(update={"retriever": "reranked"}) for c in candidates[:top_n]]

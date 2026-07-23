"""Contextual Retrieval：便宜 LLM 生成 chunk 客观定位前缀。笔记 §12.1.2。

照搬 OpenAIVisionDescriber 模式：chat completions + 缓存命中跳过 API + tenacity 重试 + 失败兜底空串。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_rag.indexing.embedder import _NonRetryable, _is_non_retryable

log = logging.getLogger(__name__)

_CONTEXTUALIZE_PROMPT = (
    "请用 50-100 字客观描述以下文档片段在整个文档中的位置和主题，"
    "用于帮助检索系统理解其上下文。仅陈述客观事实（如「位于X章节，讨论Y主题」），"
    "不要评价、不要概括、不要补充文档外的信息。"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_contexts (
    cache_key  TEXT PRIMARY KEY,
    text_hash  TEXT NOT NULL,
    model      TEXT NOT NULL,
    context    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lookup ON chunk_contexts(text_hash, model);
"""


def _cache_key(text_hash: str, model: str) -> str:
    return f"{text_hash}:{model}"


class ContextCache:
    """chunk context 缓存（照搬 ImageDescriptionCache 结构）。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ContextCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, text_hash: str, model: str) -> str | None:
        row = self._conn.execute(
            "SELECT context FROM chunk_contexts WHERE cache_key = ?",
            (_cache_key(text_hash, model),),
        ).fetchone()
        return row[0] if row else None

    def put(self, text_hash: str, model: str, context: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO chunk_contexts (cache_key, text_hash, model, context) VALUES (?, ?, ?, ?)",
            (_cache_key(text_hash, model), text_hash, model, context),
        )
        self._conn.commit()


class OpenAIContextualizer:
    """便宜 LLM 生成 chunk 客观定位前缀。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "glm-4-flash",
        max_tokens: int = 150,
        retry_stop=None,
        retry_wait=None,
    ):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens
        self._retry_stop = retry_stop or stop_after_attempt(4)
        self._retry_wait = retry_wait or wait_exponential(min=1, max=10)

    @property
    def model(self) -> str:
        return self._model

    def contextualize(
        self,
        chunk_text: str,
        doc_context: str,
        *,
        text_hash: str,
        cache: ContextCache | None = None,
    ) -> str:
        """生成 chunk 客观定位前缀。命中缓存则不调 API；失败返回空串。"""
        if cache is not None:
            cached = cache.get(text_hash, self._model)
            if cached:
                return cached
        try:
            ctx = self._call_api(chunk_text, doc_context)
        except Exception as e:  # noqa: BLE001 — 失败兜底
            log.warning("Context 生成失败，用空串兜底: %s", e)
            return ""
        if cache is not None and ctx:
            cache.put(text_hash, self._model, ctx)
        return ctx

    def _call_api(self, chunk_text: str, doc_context: str) -> str:
        prompt = f"{_CONTEXTUALIZE_PROMPT}\n\n文档背景：{doc_context}\n\n文档片段：{chunk_text}"

        @retry(
            stop=self._retry_stop,
            wait=self._retry_wait,
            retry=retry_if_not_exception_type(_NonRetryable),
            reraise=True,
        )
        def _do() -> str:
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self._max_tokens,
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise

        return _do()

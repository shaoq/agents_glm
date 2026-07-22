"""图片描述生成：智谱视觉模型（GLM-4.5V）+ 缓存 + 重试。笔记 §12.1.1 方案 A。

复用 embedder 的可/不可重试异常区分；描述缓存键 = ``content_hash + model``，
图片与模型均不变时不调 API；失败时用 caption 兜底。
"""

from __future__ import annotations

import base64
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_descriptions (
    cache_key     TEXT PRIMARY KEY,
    content_hash  TEXT NOT NULL,
    model         TEXT NOT NULL,
    description   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lookup ON image_descriptions(content_hash, model);
"""

_DESCRIBE_PROMPT = (
    "客观描述这张图片的关键信息（数据、对象、关系、场景），用于文档检索。"
    "仅陈述可见内容，不要评价、不要推测。"
)


def _cache_key(content_hash: str, model: str) -> str:
    return f"{content_hash}:{model}"


class ImageDescriptionCache:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ImageDescriptionCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, content_hash: str, model: str) -> str | None:
        row = self._conn.execute(
            "SELECT description FROM image_descriptions WHERE cache_key = ?",
            (_cache_key(content_hash, model),),
        ).fetchone()
        return row[0] if row else None

    def put(self, content_hash: str, model: str, description: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO image_descriptions (cache_key, content_hash, model, description) VALUES (?, ?, ?, ?)",
            (_cache_key(content_hash, model), content_hash, model, description),
        )
        self._conn.commit()


class ZhipuVisionDescriber:
    def __init__(
        self,
        api_key: str,
        model: str = "glm-4.5v",
        retry_stop=None,
        retry_wait=None,
    ):
        from zhipuai import ZhipuAI

        self._client = ZhipuAI(api_key=api_key)
        self._model = model
        self._retry_stop = retry_stop or stop_after_attempt(4)
        self._retry_wait = retry_wait or wait_exponential(min=1, max=10)

    @property
    def model(self) -> str:
        return self._model

    def describe(
        self,
        image_bytes: bytes,
        *,
        content_hash: str,
        fmt: str = "png",
        cache: ImageDescriptionCache | None = None,
        caption: str | None = None,
    ) -> str:
        """生成图片描述。命中缓存则不调 API；生成失败用 caption 兜底。"""
        if cache is not None:
            cached = cache.get(content_hash, self._model)
            if cached:
                return cached
        try:
            desc = self._call_api(image_bytes, fmt, caption)
        except Exception as e:  # noqa: BLE001 — 描述失败降级到 caption
            log.warning("图片描述生成失败，用 caption 兜底: %s", e)
            return (caption or "").strip()
        if cache is not None and desc:
            cache.put(content_hash, self._model, desc)
        return desc

    def _call_api(self, image_bytes: bytes, fmt: str, caption: str | None) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        mime = f"image/{fmt}"
        prompt = _DESCRIBE_PROMPT + (f"\n图注：{caption}" if caption else "")

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
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                                },
                            ],
                        }
                    ],
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise

        return _do()

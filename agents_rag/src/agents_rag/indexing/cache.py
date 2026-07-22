"""embedding 缓存（sqlite）。笔记 §6.5 / §6.13。

缓存键 = ``hash(text) + model + dim``，**版本化**——换模型 / 维度不会命中
旧向量缓存（避免向量空间错配，极隐蔽 bug）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agents_rag.ingestion.fingerprint import text_fingerprint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    cache_key  TEXT PRIMARY KEY,
    text_hash  TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lookup ON embeddings(text_hash, model, dim);
"""


def cache_key(text: str, model: str, dim: int) -> str:
    return f"{text_fingerprint(text)}:{model}:{dim}"


class EmbeddingCache:
    """文本 → 向量的持久化缓存。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EmbeddingCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, text: str, model: str, dim: int) -> list[float] | None:
        row = self._conn.execute(
            "SELECT vector FROM embeddings WHERE cache_key = ?",
            (cache_key(text, model, dim),),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, text: str, model: str, dim: int, vector: list[float]) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO embeddings (cache_key, text_hash, model, dim, vector)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cache_key(text, model, dim), text_fingerprint(text), model, dim, json.dumps(vector)),
        )
        self._conn.commit()

    def get_many(
        self, texts: list[str], model: str, dim: int
    ) -> list[list[float] | None]:
        return [self.get(t, model, dim) for t in texts]

    def delete_by_text(self, text: str, model: str, dim: int) -> None:
        """文档删除时按文本清理其缓存项。"""
        self._conn.execute(
            "DELETE FROM embeddings WHERE text_hash = ? AND model = ? AND dim = ?",
            (text_fingerprint(text), model, dim),
        )
        self._conn.commit()

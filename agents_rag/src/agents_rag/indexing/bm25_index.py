"""BM25 索引（jieba 分词 + rank_bm25，pickle 持久化）。笔记 §6.6 / §6.7。

中文 BM25 必须先分词（jieba）。rank_bm25 不支持增量，故内部维护
``id → tokens`` 字典，增删后按需重建 ``BM25Okapi``；与向量库以相同 ``chunk_id`` 对齐。
"""

from __future__ import annotations

import pickle
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from agents_rag.models import ChildChunk


def tokenize(text: str) -> list[str]:
    return [w for w in jieba.cut_for_search(text) if w.strip()]


class BM25Index:
    def __init__(self) -> None:
        self._docs: dict[str, list[str]] = {}
        self._bm25: BM25Okapi | None = None
        self._dirty: bool = True

    def _rebuild_if_dirty(self) -> None:
        if not self._dirty:
            return
        corpus = list(self._docs.values())
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._dirty = False

    def upsert(self, chunks: list[ChildChunk]) -> None:
        for c in chunks:
            self._docs[c.id] = tokenize(c.indexed_text)
        self._dirty = True

    def remove_by_ids(self, ids: list[str]) -> None:
        for i in ids:
            self._docs.pop(i, None)
        self._dirty = True

    def remove_by_doc(self, doc_id: str) -> int:
        """按 doc_id 前缀删除（chunk_id 形如 ``<doc_id>__p..__..``）。返回删除数。"""
        victims = [i for i in self._docs if i.startswith(f"{doc_id}__")]
        self.remove_by_ids(victims)
        return len(victims)

    def query(self, text: str, k: int = 10) -> list[tuple[str, float]]:
        self._rebuild_if_dirty()
        if not self._bm25:
            return []
        ids = list(self._docs.keys())
        scores = self._bm25.get_scores(tokenize(text))
        ranked = sorted(zip(ids, scores), key=lambda x: x[1], reverse=True)
        return [(i, float(s)) for i, s in ranked[:k] if s > 0]

    def count(self) -> int:
        return len(self._docs)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self._docs, f)

    @classmethod
    def load(cls, path: str | Path) -> BM25Index:
        idx = cls()
        with open(path, "rb") as f:
            idx._docs = pickle.load(f)  # noqa: S301 — 自有数据
        idx._dirty = True
        return idx

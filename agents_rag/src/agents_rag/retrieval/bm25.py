"""BM25 检索：BM25Index.query → ChromaStore.get_by_ids 补 metadata。笔记 §3.1。

BM25 无 where 参数（status 不可过滤），但构建侧 update 物理删旧保证无脏数据。
BM25Index.query 返回 [(id, score)]；text/metadata 从 ChromaStore.get_by_ids 补取。
"""

from __future__ import annotations

from agents_rag.indexing.bm25_index import BM25Index
from agents_rag.indexing.chroma_store import ChromaStore
from agents_rag.models import RetrievalResult
from agents_rag.retrieval.base import Retriever
from agents_rag.retrieval.vector import _from_metadata


class BM25Retriever(Retriever):
    def __init__(self, bm25: BM25Index, store: ChromaStore):
        self._bm25 = bm25
        self._store = store

    def retrieve(self, query: str, k: int = 20) -> list[RetrievalResult]:
        hits = self._bm25.query(query, k=k)
        if not hits:
            return []
        ids = [h[0] for h in hits]
        details = {d["id"]: d for d in self._store.get_by_ids(ids)}
        results: list[RetrievalResult] = []
        for cid, score in hits:
            d = details.get(cid)
            if d is None:
                continue
            results.append(_from_metadata(d, score, "bm25"))
        return results

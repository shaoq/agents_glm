"""向量检索：embed query → ChromaStore.query_detailed → RetrievalResult。笔记 §3.1。

distance（小=近）转为 score=-distance（大=好），统一方向。
status pre-filter：where={"status": "active"}。
"""

from __future__ import annotations

from agents_rag.indexing.chroma_store import ChromaStore
from agents_rag.indexing.embedder import Embedder
from agents_rag.models import RetrievalResult
from agents_rag.retrieval.base import Retriever


def _meta_page(meta: dict) -> int | None:
    p = meta.get("page", -1)
    return p if p != -1 else None


def _from_metadata(r: dict, score: float, retriever: str) -> RetrievalResult:
    meta = r.get("metadata", {})
    return RetrievalResult(
        chunk_id=r["id"],
        text=r.get("document", ""),
        score=score,
        retriever=retriever,
        doc_id=meta.get("doc_id", ""),
        parent_id=meta.get("parent_id", ""),
        page=_meta_page(meta),
        heading=meta.get("heading") or None,
        section_path=meta.get("section_path", ""),
        image_ref=meta.get("image_ref") or None,
    )


class VectorRetriever(Retriever):
    def __init__(self, embedder: Embedder, store: ChromaStore):
        self._embedder = embedder
        self._store = store

    def retrieve(self, query: str, k: int = 20) -> list[RetrievalResult]:
        vec = self._embedder.embed([query])[0]
        results = self._store.query_detailed(vec, k=k, where={"status": "active"})
        return [_from_metadata(r, -r["distance"], "vector") for r in results]

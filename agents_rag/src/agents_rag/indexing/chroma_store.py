"""Chroma 向量库（PersistentClient 落盘，HNSW）。笔记 §6.8。"""

from __future__ import annotations

from pathlib import Path

import chromadb

from agents_rag.indexing.vectorstore import VectorStore
from agents_rag.models import ChildChunk


class ChromaStore(VectorStore):
    def __init__(self, path: str | Path, collection_name: str = "chunks"):
        self._client = chromadb.PersistentClient(path=str(path))
        self._col = self._client.get_or_create_collection(name=collection_name)

    def upsert(self, chunks: list[ChildChunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        self._col.upsert(
            ids=[c.id for c in chunks],
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=[c.metadata_dict() for c in chunks],
        )

    def delete_by_doc(self, doc_id: str) -> None:
        self._col.delete(where={"doc_id": doc_id})

    def delete_by_ids(self, ids: list[str]) -> None:
        if ids:
            self._col.delete(ids=ids)

    def query(
        self, vector: list[float], k: int = 10, where: dict | None = None
    ) -> list[tuple[str, float]]:
        res = self._col.query(query_embeddings=[vector], n_results=k, where=where)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return list(zip(ids, dists))

    def count(self) -> int:
        return self._col.count()

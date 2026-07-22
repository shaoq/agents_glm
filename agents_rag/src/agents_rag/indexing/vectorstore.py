"""VectorStore 抽象。笔记 §6.8（抽象接口便于规模化迁移 Qdrant）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agents_rag.models import ChildChunk


class VectorStore(ABC):
    """向量库接口：upsert / 删除 / 查询。"""

    @abstractmethod
    def upsert(self, chunks: list[ChildChunk], vectors: list[list[float]]) -> None: ...

    @abstractmethod
    def delete_by_doc(self, doc_id: str) -> None: ...

    @abstractmethod
    def delete_by_ids(self, ids: list[str]) -> None: ...

    @abstractmethod
    def query(
        self, vector: list[float], k: int = 10, where: dict | None = None
    ) -> list[tuple[str, float]]:
        """近邻查询，返回 [(chunk_id, distance), ...]。"""

    @abstractmethod
    def count(self) -> int: ...

from pathlib import Path
from typing import Protocol

import chromadb
from pydantic import BaseModel, ConfigDict, Field

from agents_memory.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Validity,
)


class IndexModelMismatch(ValueError):
    pass


class IndexHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: str
    similarity: float = Field(ge=0, le=1)


class MemoryIndex(Protocol):
    def upsert(self, record: MemoryRecord, embedding: list[float]) -> None: ...

    def delete(self, memory_id: str) -> None: ...

    def list_ids(self) -> list[str]: ...

    def query(
        self,
        embedding: list[float],
        *,
        scope: MemoryScope,
        type: MemoryType,
        top_k: int,
        threshold: float,
    ) -> list[IndexHit]: ...

    def query_candidates(
        self,
        embedding: list[float],
        *,
        user_id: str,
        types: tuple[MemoryType, ...],
        top_k: int,
        agent_id: str | None = None,
        session_id: str | None = None,
        validities: tuple[Validity, ...] = (Validity.ACTIVE,),
        threshold: float = 0.0,
    ) -> list[IndexHit]: ...


class ChromaMemoryIndex:
    collection_name = "agents_memory"

    def __init__(self, path: Path | str, *, model: str, dimension: int) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.dimension = dimension
        self.client = chromadb.PersistentClient(path=str(self.path))
        self.collection = self.client.get_or_create_collection(
            self.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": model,
                "embedding_dimension": dimension,
            },
        )
        metadata = self.collection.metadata or {}
        if (
            metadata.get("embedding_model") != model
            or int(metadata.get("embedding_dimension", -1)) != dimension
        ):
            raise IndexModelMismatch(
                "existing Chroma collection uses a different embedding model or dimension"
            )

    def upsert(self, record: MemoryRecord, embedding: list[float]) -> None:
        if len(embedding) != self.dimension:
            raise IndexModelMismatch(
                f"expected embedding dimension {self.dimension}, got {len(embedding)}"
            )
        self.collection.upsert(
            ids=[record.id],
            documents=[record.content],
            embeddings=[embedding],
            metadatas=[
                {
                    "user_id": record.scope.user_id,
                    "agent_id": record.scope.agent_id or "",
                    "session_id": record.scope.session_id or "",
                    "type": record.type.value,
                    "validity": record.validity.value,
                    "created_at": record.created_at.isoformat(),
                }
            ],
        )

    def delete(self, memory_id: str) -> None:
        self.collection.delete(ids=[memory_id])

    def query(
        self,
        embedding: list[float],
        *,
        scope: MemoryScope,
        type: MemoryType,
        top_k: int,
        threshold: float,
    ) -> list[IndexHit]:
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={
                "$and": [
                    {"user_id": {"$eq": scope.user_id}},
                    {"agent_id": {"$eq": scope.agent_id or ""}},
                    {"session_id": {"$eq": scope.session_id or ""}},
                    {"type": {"$eq": type.value}},
                    {"validity": {"$eq": "active"}},
                ]
            },
            include=["distances"],
        )
        ids = result["ids"][0] if result["ids"] else []
        distances = result["distances"][0] if result["distances"] else []
        hits = []
        for memory_id, distance in zip(ids, distances, strict=True):
            similarity = max(0.0, min(1.0, 1.0 - float(distance)))
            if similarity >= threshold:
                hits.append(IndexHit(memory_id=memory_id, similarity=similarity))
        return hits

    def query_candidates(
        self,
        embedding: list[float],
        *,
        user_id: str,
        types: tuple[MemoryType, ...],
        top_k: int,
        agent_id: str | None = None,
        session_id: str | None = None,
        validities: tuple[Validity, ...] = (Validity.ACTIVE,),
        threshold: float = 0.0,
    ) -> list[IndexHit]:
        clauses: list[dict[str, object]] = [
            {"user_id": {"$eq": user_id}},
            {"type": {"$in": [t.value for t in types]}},
            {"validity": {"$in": [v.value for v in validities]}},
        ]
        if agent_id is not None:
            clauses.append({"agent_id": {"$eq": agent_id}})
        if session_id is not None:
            clauses.append({"session_id": {"$eq": session_id}})
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"$and": clauses},
            include=["distances"],
        )
        ids = result["ids"][0] if result["ids"] else []
        distances = result["distances"][0] if result["distances"] else []
        hits = []
        for memory_id, distance in zip(ids, distances, strict=True):
            similarity = max(0.0, min(1.0, 1.0 - float(distance)))
            if similarity >= threshold:
                hits.append(IndexHit(memory_id=memory_id, similarity=similarity))
        return hits

    def count(self) -> int:
        return self.collection.count()

    def list_ids(self) -> list[str]:
        return list(self.collection.get(include=[])["ids"])

    def clear(self) -> None:
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            self.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self.model,
                "embedding_dimension": self.dimension,
            },
        )

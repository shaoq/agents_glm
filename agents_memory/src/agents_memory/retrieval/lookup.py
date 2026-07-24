from pydantic import BaseModel, ConfigDict, Field

from agents_memory.embedding.base import Embedder
from agents_memory.models import MemoryRecord, MemoryScope, MemoryType, Validity
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import MemoryIndex


class IndexLookupError(RuntimeError):
    pass


class HistoricalMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: MemoryRecord
    similarity: float = Field(ge=0, le=1)


class LookupResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    embedding: list[float]
    matches: tuple[HistoricalMatch, ...]


class ContextLookup:
    def __init__(
        self,
        *,
        embedder: Embedder,
        index: MemoryIndex,
        repository: MemoryRepository,
        top_k: int,
        threshold: float,
    ) -> None:
        self.embedder = embedder
        self.index = index
        self.repository = repository
        self.top_k = top_k
        self.threshold = threshold

    def lookup(
        self, content: str, scope: MemoryScope, type: MemoryType
    ) -> LookupResult:
        embedding = self.embedder.embed([content])[0]
        try:
            hits = self.index.query(
                embedding,
                scope=scope,
                type=type,
                top_k=self.top_k,
                threshold=self.threshold,
            )
        except Exception as exc:
            raise IndexLookupError(str(exc)) from exc
        matches: list[HistoricalMatch] = []
        seen: set[str] = set()
        for hit in hits:
            record = self.repository.get_memory(hit.memory_id)
            if (
                record is not None
                and record.scope == scope
                and record.type is type
                and record.validity is Validity.ACTIVE
            ):
                matches.append(HistoricalMatch(record=record, similarity=hit.similarity))
                seen.add(record.id)
        for record in self.repository.list_unsynced_active_memories(scope, type):
            if record.id not in seen:
                matches.append(HistoricalMatch(record=record, similarity=0.0))
        return LookupResult(embedding=embedding, matches=tuple(matches))

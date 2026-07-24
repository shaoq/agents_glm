from typing import Protocol

from agents_memory.models import CandidateMemory, MemoryRecord, RelationMatch


class RelationResolver(Protocol):
    def resolve(
        self, candidate: CandidateMemory, histories: list[MemoryRecord]
    ) -> list[RelationMatch]: ...


class FakeRelationResolver:
    def __init__(self, relations: list[RelationMatch] | None = None) -> None:
        self.relations = relations or []
        self.calls = 0

    def resolve(
        self, candidate: CandidateMemory, histories: list[MemoryRecord]
    ) -> list[RelationMatch]:
        self.calls += 1
        allowed = {record.id for record in histories}
        return [item for item in self.relations if item.memory_id in allowed]

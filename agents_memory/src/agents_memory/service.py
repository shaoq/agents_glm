from pydantic import BaseModel, ConfigDict

from agents_memory.models import (
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    MemoryType,
)
from agents_memory.storage.coordinator import StorageCoordinator
from agents_memory.storage.repository import MemoryRepository


class MemoryDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: MemoryRecord
    sources: tuple[MemorySource, ...]
    relations: tuple[MemoryRelation, ...]
    history: tuple[MemoryRecord, ...]


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        coordinator: StorageCoordinator | None = None,
    ) -> None:
        self.repository = repository
        self.coordinator = coordinator

    def list_memories(
        self,
        scope: MemoryScope,
        type: MemoryType | None = None,
        *,
        include_history: bool = False,
    ) -> list[MemoryRecord]:
        return self.repository.list_memories(
            scope, type, include_history=include_history
        )

    def get_memory(self, memory_id: str, user_id: str) -> MemoryDetail | None:
        record = self.repository.get_memory(memory_id)
        if record is None or record.scope.user_id != user_id:
            return None
        relations = tuple(self.repository.get_relations(memory_id))
        history_ids = {
            item.from_memory_id if item.to_memory_id == memory_id else item.to_memory_id
            for item in relations
        }
        return MemoryDetail(
            record=record,
            sources=tuple(self.repository.get_sources(memory_id)),
            relations=relations,
            history=tuple(self.repository.get_memories(sorted(history_ids))),
        )

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        if self.coordinator is None:
            raise RuntimeError("storage coordinator is required")
        return self.coordinator.delete_memory(memory_id, user_id)

    def repair_index(self) -> int:
        if self.coordinator is None:
            raise RuntimeError("storage coordinator is required")
        return self.coordinator.repair_index()

    def rebuild_index(self) -> int:
        if self.coordinator is None:
            raise RuntimeError("storage coordinator is required")
        return self.coordinator.rebuild_index()

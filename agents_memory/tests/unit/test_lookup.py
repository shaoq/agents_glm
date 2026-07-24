from pathlib import Path

from agents_memory.embedding.base import FakeEmbedder
from agents_memory.models import (
    IndexOperationKind,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Validity,
)
from agents_memory.retrieval.lookup import ContextLookup
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import IndexHit


class StubIndex:
    def query(self, *_args, **_kwargs):
        return [
            IndexHit(memory_id="active", similarity=0.9),
            IndexHit(memory_id="history", similarity=0.85),
            IndexHit(memory_id="missing", similarity=0.8),
        ]


def record(memory_id: str, validity: Validity) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id="u1"),
        type=MemoryType.FACT,
        content=memory_id,
        importance=7,
        confidence=0.9,
        validity=validity,
    )


def test_lookup_hydrates_only_active_exact_scope_records(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    repository.save_memory(record("active", Validity.ACTIVE))
    repository.save_memory(record("history", Validity.SUPERSEDED))
    lookup = ContextLookup(
        embedder=FakeEmbedder(dimension=3),
        index=StubIndex(),
        repository=repository,
        top_k=5,
        threshold=0.7,
    )

    result = lookup.lookup(
        "candidate", MemoryScope(user_id="u1"), MemoryType.FACT
    )

    assert [item.record.id for item in result.matches] == ["active"]
    assert len(result.embedding) == 3


def test_lookup_includes_active_truth_with_failed_upsert(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    repository.save_memory(record("unsynced", Validity.ACTIVE))
    repository.enqueue_index_operation(
        "req-1", "unsynced", IndexOperationKind.UPSERT
    )
    lookup = ContextLookup(
        embedder=FakeEmbedder(dimension=3),
        index=StubIndex(),
        repository=repository,
        top_k=5,
        threshold=0.7,
    )

    result = lookup.lookup("candidate", MemoryScope(user_id="u1"), MemoryType.FACT)

    assert "unsynced" in [item.record.id for item in result.matches]

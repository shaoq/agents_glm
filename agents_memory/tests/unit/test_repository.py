from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents_memory.models import (
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    MemoryType,
    RelationKind,
    SourceKind,
    Validity,
    WriteReport,
    WriteStatus,
)
from agents_memory.storage.repository import IdempotencyConflict, MemoryRepository


def make_record(
    memory_id: str,
    *,
    user_id: str = "u1",
    type: MemoryType = MemoryType.FACT,
    validity: Validity = Validity.ACTIVE,
) -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id),
        type=type,
        content=f"content-{memory_id}",
        importance=7,
        confidence=0.9,
        validity=validity,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def repository(tmp_path: Path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "memory.sqlite")


def test_repository_persists_memory_source_and_exact_scope(
    repository: MemoryRepository,
) -> None:
    record = make_record("m1")
    source = MemorySource(
        memory_id="m1",
        message_id="msg-1",
        role="user",
        source_kind=SourceKind.USER_EXPLICIT,
        excerpt="original",
    )

    repository.save_memory(record, (source,))

    assert repository.get_memory("m1") == record
    assert repository.get_sources("m1") == [source]
    assert repository.list_memories(MemoryScope(user_id="u1"), MemoryType.FACT) == [record]
    assert repository.list_memories(MemoryScope(user_id="u2"), MemoryType.FACT) == []
    assert repository.list_memories(MemoryScope(user_id="u1"), MemoryType.EVENT) == []


def test_transition_preserves_history_and_relation(repository: MemoryRepository) -> None:
    old = make_record("old")
    new = make_record("new")
    repository.save_memory(old)
    repository.save_memory(new)
    relation = MemoryRelation(
        from_memory_id="old",
        to_memory_id="new",
        relation=RelationKind.SUPERSEDES,
    )

    repository.transition("old", Validity.SUPERSEDED, relation)

    assert repository.get_memory("old").validity is Validity.SUPERSEDED  # type: ignore[union-attr]
    assert repository.list_memories(MemoryScope(user_id="u1"), include_history=False) == [new]
    assert {record.id for record in repository.list_memories(
        MemoryScope(user_id="u1"), include_history=True
    )} == {"old", "new"}
    assert repository.get_relations("new") == [relation]


def test_request_snapshot_enforces_input_hash(repository: MemoryRepository) -> None:
    report = WriteReport(
        request_id="req-1",
        status=WriteStatus.SUCCESS,
        sqlite_committed=True,
        index_synced=True,
    )

    repository.save_request("req-1", "hash-a", "committed", report)

    stored = repository.get_request("req-1", "hash-a")
    assert stored is not None
    assert stored.report == report
    with pytest.raises(IdempotencyConflict):
        repository.get_request("req-1", "hash-b")


def test_index_operations_move_through_retry_states(repository: MemoryRepository) -> None:
    operation_id = repository.enqueue_index_operation(
        request_id="req-1",
        memory_id="m1",
        kind=IndexOperationKind.UPSERT,
    )

    operation = repository.list_index_operations()[0]
    assert operation.id == operation_id
    assert operation.status is IndexOperationStatus.PENDING

    repository.mark_index_operation(operation_id, IndexOperationStatus.FAILED, "offline")
    failed = repository.list_index_operations(IndexOperationStatus.FAILED)[0]
    assert failed.attempts == 1
    assert failed.last_error == "offline"

    repository.mark_index_operation(operation_id, IndexOperationStatus.SYNCED)
    assert repository.list_index_operations(IndexOperationStatus.SYNCED)[0].attempts == 2


def test_transaction_rolls_back_all_changes(repository: MemoryRepository) -> None:
    with pytest.raises(RuntimeError):
        with repository.transaction() as connection:
            repository.save_memory(make_record("m1"), connection=connection)
            raise RuntimeError("boom")

    assert repository.get_memory("m1") is None

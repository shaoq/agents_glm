from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents_memory.models import (
    CandidateMemory,
    EventFrame,
    EventStatus,
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    MemoryType,
    PendingResolution,
    PendingResolutionStatus,
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


def test_repository_migrates_v1_and_preserves_event_frame(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    connection = __import__("sqlite3").connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (1);
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_id TEXT,
            session_id TEXT, type TEXT NOT NULL, content TEXT NOT NULL,
            importance INTEGER NOT NULL, confidence REAL NOT NULL,
            validity TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, valid_from TEXT, metadata_json TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()

    migrated = MemoryRepository(path)
    migrated_again = MemoryRepository(path)
    event = make_record("event-1", type=MemoryType.EVENT).model_copy(
        update={
            "event_frame": EventFrame(
                actor="user", predicate="travel", status=EventStatus.PLANNED
            )
        }
    )
    migrated_again.save_memory(event)

    assert migrated.get_memory("event-1") == event
    check = __import__("sqlite3").connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    columns = {row[1] for row in check.execute("PRAGMA table_info(memories)")}
    check.close()
    assert "event_frame_json" in columns


def test_repository_persists_pending_and_isolates_exact_scope(
    repository: MemoryRepository,
) -> None:
    now = datetime.now(UTC)
    candidate = CandidateMemory(
        content="用户不去北京了",
        type=MemoryType.EVENT,
        importance=8,
        confidence=0.8,
        source_message_ids=("msg-1",),
        source_kind=SourceKind.USER_EXPLICIT,
        event_frame=EventFrame(status=EventStatus.CANCELLED),
    )
    pending = PendingResolution(
        id="pr-1",
        scope=MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
        candidate=candidate,
        conflicting_memory_ids=("old",),
        semantic_relation=RelationKind.CONTRADICT,
        missing_dimensions=("event_time",),
        source_message_ids=("msg-1",),
        processed_evidence_message_ids=("msg-1",),
        importance=8,
        status=PendingResolutionStatus.OPEN,
        created_at=now,
        updated_at=now,
        last_evaluated_at=now,
        expires_at=now,
    )

    repository.save_pending_resolution(pending)

    assert repository.get_pending_resolution("pr-1") == pending
    assert repository.list_pending_resolutions(pending.scope) == [pending]
    assert repository.list_pending_resolutions(
        MemoryScope(user_id="u1", agent_id="a1", session_id="other")
    ) == []

    resolved = pending.model_copy(
        update={"status": PendingResolutionStatus.RESOLVED}
    )
    repository.save_pending_resolution(resolved)
    assert repository.get_pending_resolution("pr-1") == resolved
    with pytest.raises(ValueError, match="terminal pending resolution"):
        repository.save_pending_resolution(pending)


def test_repository_sweep_expires_open_pending_without_semantic_work(
    repository: MemoryRepository,
) -> None:
    now = datetime.now(UTC)
    pending = PendingResolution(
        id="expired",
        scope=MemoryScope(user_id="u1"),
        candidate=CandidateMemory(
            content="后来取消了",
            type=MemoryType.EVENT,
            importance=3,
            confidence=0.8,
            source_message_ids=("m1",),
            source_kind=SourceKind.USER_EXPLICIT,
        ),
        semantic_relation=RelationKind.CONTRADICT,
        importance=3,
        expires_at=now - timedelta(seconds=1),
    )
    repository.save_pending_resolution(pending)

    changed = repository.sweep_pending_resolutions(now=now)

    assert changed == 1
    stored = repository.get_pending_resolution("expired")
    assert stored is not None
    assert stored.status is PendingResolutionStatus.EXPIRED
    assert repository.list_pending_resolutions(pending.scope) == []


def test_repository_cleans_only_old_terminal_pending(
    repository: MemoryRepository,
) -> None:
    now = datetime.now(UTC)
    base = PendingResolution(
        id="base",
        scope=MemoryScope(user_id="u1"),
        candidate=CandidateMemory(
            content="后来取消了",
            type=MemoryType.EVENT,
            importance=3,
            confidence=0.8,
            source_message_ids=("m1",),
            source_kind=SourceKind.USER_EXPLICIT,
        ),
        semantic_relation=RelationKind.CONTRADICT,
        importance=3,
        created_at=now - timedelta(days=40),
        updated_at=now - timedelta(days=40),
        expires_at=now - timedelta(days=30),
    )
    repository.save_pending_resolution(
        base.model_copy(
            update={
                "id": "old-expired",
                "status": PendingResolutionStatus.EXPIRED,
            }
        )
    )
    repository.save_pending_resolution(
        base.model_copy(
            update={
                "id": "recent-resolved",
                "status": PendingResolutionStatus.RESOLVED,
                "updated_at": now,
            }
        )
    )
    repository.save_pending_resolution(
        base.model_copy(
            update={"id": "still-open", "status": PendingResolutionStatus.OPEN}
        )
    )

    deleted = repository.cleanup_pending_resolutions(
        before=now - timedelta(days=7)
    )

    assert deleted == 1
    assert repository.get_pending_resolution("old-expired") is None
    assert repository.get_pending_resolution("recent-resolved") is not None
    assert repository.get_pending_resolution("still-open") is not None

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents_memory.embedding.base import FakeEmbedder
from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    EventFrame,
    EventStatus,
    IndexOperationStatus,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Message,
    PendingResolution,
    PendingResolutionStatus,
    RelationKind,
    SourceKind,
    Validity,
    WriteStatus,
)
from agents_memory.storage.coordinator import RequestAlreadyReserved, StorageCoordinator
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import IndexModelMismatch


class ToggleIndex:
    def __init__(self) -> None:
        self.fail = False
        self.items: dict[str, object] = {}

    def upsert(self, record, embedding):
        if self.fail:
            raise RuntimeError("offline")
        self.items[record.id] = (record, embedding)

    def delete(self, memory_id):
        if self.fail:
            raise RuntimeError("offline")
        self.items.pop(memory_id, None)

    def clear(self):
        self.items.clear()


def candidate() -> CandidateMemory:
    return CandidateMemory(
        content="用户偏好 Python",
        type=MemoryType.FACT,
        importance=8,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
    )


def test_coordinator_commits_sqlite_then_syncs_index(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    plan = ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)

    report = coordinator.commit(
        request_id="req-1",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[plan],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )

    assert report.status is WriteStatus.SUCCESS
    assert report.sqlite_committed and report.index_synced
    memory_id = report.results[0].memory_id
    assert repository.get_memory(memory_id) is not None
    assert memory_id in index.items


def test_coordinator_preserves_truth_and_repairs_failed_index(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    index.fail = True
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    plan = ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)

    report = coordinator.commit(
        request_id="req-1",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[plan],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )

    assert report.status is WriteStatus.RETRYABLE
    assert report.sqlite_committed and not report.index_synced
    assert repository.get_memory(report.results[0].memory_id) is not None

    index.fail = False
    repaired = coordinator.repair_request("req-1", "hash")
    assert repaired.status is WriteStatus.SUCCESS
    assert repaired.index_synced


def test_coordinator_rebuilds_only_active_and_deletes_owned_memory(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    plan = ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)
    report = coordinator.commit(
        request_id="req-1",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[plan],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )
    memory_id = report.results[0].memory_id
    index.items.clear()

    assert coordinator.rebuild_index() == 1
    assert memory_id in index.items
    assert not coordinator.delete_memory(memory_id, "other-user")
    assert coordinator.delete_memory(memory_id, "u1")
    assert repository.get_memory(memory_id) is None
    assert memory_id not in index.items


def test_failed_delete_is_repaired_without_write_request(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    report = coordinator.commit(
        request_id="req-1",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )
    memory_id = report.results[0].memory_id
    index.fail = True
    assert coordinator.delete_memory(memory_id, "u1")

    index.fail = False
    assert coordinator.repair_index() == 1
    assert memory_id not in index.items


def test_repair_treats_upsert_of_already_deleted_truth_as_converged(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    index.fail = True
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    report = coordinator.commit(
        request_id="req-add",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )
    memory_id = report.results[0].memory_id
    assert coordinator.delete_memory(memory_id, "u1")

    index.fail = False
    coordinator.repair_index()

    assert all(
        operation.status is IndexOperationStatus.SYNCED
        for operation in repository.list_index_operations()
    )


def test_failed_rebuild_does_not_clear_live_index_first(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    report = coordinator.commit(
        request_id="req-1",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )
    memory_id = report.results[0].memory_id
    index.fail = True

    with pytest.raises(RuntimeError):
        coordinator.rebuild_index()

    assert memory_id in index.items


def test_coordinator_reserves_request_before_business_writes(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    repository.reserve_request("req-1", "hash")
    coordinator = StorageCoordinator(repository, ToggleIndex(), FakeEmbedder(3))

    with pytest.raises(RequestAlreadyReserved):
        coordinator.commit(
            request_id="req-1",
            input_hash="hash",
            scope=MemoryScope(user_id="u1"),
            plans=[ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)],
            messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
            embeddings={0: [1.0, 0.0, 0.0]},
            extracted_count=1,
            filtered_count=0,
        )

    assert repository.list_memories(MemoryScope(user_id="u1")) == []


def test_nonretryable_index_mismatch_is_not_repair_loop(tmp_path: Path) -> None:
    class MismatchedIndex(ToggleIndex):
        def upsert(self, record, embedding):
            raise IndexModelMismatch("wrong dimension")

    repository = MemoryRepository(tmp_path / "memory.sqlite")
    coordinator = StorageCoordinator(repository, MismatchedIndex(), FakeEmbedder(3))

    report = coordinator.commit(
        request_id="req-1",
        input_hash="hash",
        scope=MemoryScope(user_id="u1"),
        plans=[ActionPlan(candidate_index=0, candidate=candidate(), action=Action.ADD)],
        messages=[Message(message_id="m1", role="user", content="我喜欢 Python")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )

    assert report.status is WriteStatus.FAILED
    assert not report.retryable
    assert coordinator.repair_index() == 0


def test_defer_persists_resolution_without_memory_or_index_operation(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = ToggleIndex()
    coordinator = StorageCoordinator(repository, index, FakeEmbedder(3))
    now = datetime.now(UTC)
    deferred_candidate = candidate().model_copy(
        update={
            "type": MemoryType.EVENT,
            "content": "用户不去北京了",
            "event_frame": EventFrame(status=EventStatus.CANCELLED),
        }
    )
    pending = PendingResolution(
        id="pr-1",
        scope=MemoryScope(user_id="u1"),
        candidate=deferred_candidate,
        conflicting_memory_ids=("old",),
        semantic_relation=RelationKind.CONTRADICT,
        missing_dimensions=("event_time",),
        source_message_ids=("m1",),
        processed_evidence_message_ids=("m1",),
        importance=8,
        status=PendingResolutionStatus.OPEN,
        created_at=now,
        updated_at=now,
        last_evaluated_at=now,
        expires_at=now + timedelta(days=30),
    )

    report = coordinator.commit(
        request_id="req-defer",
        input_hash="hash",
        scope=pending.scope,
        plans=[
            ActionPlan(
                candidate_index=0,
                candidate=deferred_candidate,
                action=Action.DEFER,
                target_ids=("old",),
                pending_resolution=pending,
            )
        ],
        messages=[Message(message_id="m1", role="user", content="不去了")],
        embeddings={0: [1.0, 0.0, 0.0]},
        extracted_count=1,
        filtered_count=0,
    )

    assert report.status is WriteStatus.SUCCESS
    assert report.results[0].action is Action.DEFER
    assert report.results[0].resolution_id == "pr-1"
    assert repository.get_pending_resolution("pr-1") == pending
    assert repository.list_memories(pending.scope) == []
    assert repository.list_index_operations() == []
    assert index.items == {}


def test_pending_and_memory_changes_roll_back_together_on_precommit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    coordinator = StorageCoordinator(repository, ToggleIndex(), FakeEmbedder(3))
    now = datetime.now(UTC)
    deferred_candidate = candidate().model_copy(
        update={"type": MemoryType.EVENT}
    )
    pending = PendingResolution(
        id="pr-rollback",
        scope=MemoryScope(user_id="u1"),
        candidate=deferred_candidate,
        semantic_relation=RelationKind.CONTRADICT,
        importance=8,
        expires_at=now + timedelta(days=30),
    )

    def fail_save_request(*_args, **_kwargs):
        raise RuntimeError("stop before commit")

    monkeypatch.setattr(repository, "save_request", fail_save_request)

    with pytest.raises(RuntimeError, match="stop before commit"):
        coordinator.commit(
            request_id="req-rollback",
            input_hash="hash",
            scope=pending.scope,
            plans=[
                ActionPlan(
                    candidate_index=0,
                    candidate=deferred_candidate,
                    action=Action.DEFER,
                    pending_resolution=pending,
                ),
                ActionPlan(
                    candidate_index=1,
                    candidate=candidate(),
                    action=Action.ADD,
                ),
            ],
            messages=[
                Message(message_id="m1", role="user", content="mixed")
            ],
            embeddings={1: [1.0, 0.0, 0.0]},
            extracted_count=2,
            filtered_count=0,
        )

    assert repository.get_pending_resolution("pr-rollback") is None
    assert repository.list_memories(pending.scope) == []
    assert repository.list_index_operations() == []


def test_update_rolls_back_when_target_is_no_longer_active(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    old = MemoryRecord(
        id="old",
        scope=MemoryScope(user_id="u1"),
        type=MemoryType.FACT,
        content="old",
        importance=8,
        confidence=0.9,
        validity=Validity.SUPERSEDED,
    )
    repository.save_memory(old)
    coordinator = StorageCoordinator(repository, ToggleIndex(), FakeEmbedder(3))

    with pytest.raises(RuntimeError, match="active"):
        coordinator.commit(
            request_id="req-stale",
            input_hash="hash",
            scope=old.scope,
            plans=[
                ActionPlan(
                    candidate_index=0,
                    candidate=candidate(),
                    action=Action.UPDATE,
                    target_ids=("old",),
                    relation=RelationKind.CONTRADICT,
                )
            ],
            messages=[
                Message(message_id="m1", role="user", content="new")
            ],
            embeddings={0: [1.0, 0.0, 0.0]},
            extracted_count=1,
            filtered_count=0,
        )

    assert len(repository.list_memories(old.scope, include_history=True)) == 1

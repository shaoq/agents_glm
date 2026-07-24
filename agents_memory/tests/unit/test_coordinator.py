from pathlib import Path

import pytest

from agents_memory.embedding.base import FakeEmbedder
from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    IndexOperationStatus,
    MemoryScope,
    MemoryType,
    Message,
    SourceKind,
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

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_memory.models import (
    CandidateMemory,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    PendingResolution,
    RelationKind,
    SourceKind,
)
from agents_memory.service import MemoryService
from agents_memory.storage.repository import MemoryRepository


class StubCoordinator:
    def __init__(self):
        self.deleted = []

    def delete_memory(self, memory_id, user_id):
        self.deleted.append((memory_id, user_id))
        return True

    def repair_index(self):
        return 2

    def rebuild_index(self):
        return 3


def test_service_lists_shows_deletes_and_syncs(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    record = MemoryRecord(
        id="m1",
        scope=MemoryScope(user_id="u1"),
        type=MemoryType.FACT,
        content="用户偏好 Python",
        importance=8,
        confidence=0.9,
    )
    repository.save_memory(record)
    coordinator = StubCoordinator()
    service = MemoryService(repository, coordinator)

    assert service.list_memories(MemoryScope(user_id="u1")) == [record]
    detail = service.get_memory("m1", "u1")
    assert detail is not None and detail.record == record
    assert detail.history == ()
    assert service.get_memory("m1", "u2") is None
    assert service.delete_memory("m1", "u1")
    assert coordinator.deleted == [("m1", "u1")]
    assert service.repair_index() == 2
    assert service.rebuild_index() == 3


def test_service_observes_and_sweeps_pending_by_exact_scope(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    now = datetime.now(UTC)
    pending = PendingResolution(
        id="pr-1",
        scope=MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
        candidate=CandidateMemory(
            content="后来取消了",
            type=MemoryType.EVENT,
            importance=4,
            confidence=0.8,
            source_message_ids=("m1",),
            source_kind=SourceKind.USER_EXPLICIT,
        ),
        semantic_relation=RelationKind.CONTRADICT,
        importance=4,
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(seconds=1),
    )
    repository.save_pending_resolution(pending)
    service = MemoryService(repository)

    views = service.list_pending_resolutions(pending.scope, now=now)

    assert len(views) == 1
    assert views[0].resolution_id == "pr-1"
    assert views[0].age_seconds == 7200
    assert (
        service.list_pending_resolutions(MemoryScope(user_id="u1"), now=now)
        == []
    )
    assert service.sweep_pending_resolutions(now=now) == 1

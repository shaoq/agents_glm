"""Tests for RecallReadRepository: additive, user-bound, bounded read queries.

These cover task 2.2/2.4/2.6: batch loading, scoped reads, temporal reads,
historical versions, batch relations, unsynced coverage and final-state
revalidation. All queries enforce user_id and a hard limit; none mutate state.
"""

from datetime import UTC, datetime

import pytest

from agents_memory.models import (
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    RelationKind,
    Validity,
)
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.repository import MemoryRepository


def _record(
    memory_id: str,
    *,
    user_id: str = "u1",
    agent_id: str | None = "a1",
    session_id: str | None = "s1",
    type_: MemoryType = MemoryType.FACT,
    validity: Validity = Validity.ACTIVE,
    content: str = "c",
    valid_from: datetime | None = None,
    created_at: datetime | None = None,
) -> MemoryRecord:
    record = MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id, agent_id=agent_id, session_id=session_id),
        type=type_,
        content=content,
        importance=5,
        confidence=0.8,
        validity=validity,
        valid_from=valid_from,
    )
    if created_at is not None:
        return record.model_copy(update={"created_at": created_at, "updated_at": created_at})
    return record


def _insert_relation(
    repo: MemoryRepository, from_id: str, to_id: str, relation: RelationKind
) -> None:
    with repo._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO memory_relations (from_memory_id, to_memory_id, relation, created_at)"
            " VALUES (?, ?, ?, ?)",
            (from_id, to_id, relation.value, datetime.now(UTC).isoformat()),
        )
        conn.commit()


@pytest.fixture()
def repo(tmp_path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "test.db")


@pytest.fixture()
def reader(repo) -> RecallReadRepository:
    return RecallReadRepository(repo.path)


class TestLoadMemoriesByIds:
    def test_batch_loads_only_requested_ids(self, repo, reader):
        repo.save_memory(_record("m1"))
        repo.save_memory(_record("m2"))
        repo.save_memory(_record("m3"))
        loaded = reader.load_memories_by_ids(("m1", "m3"), user_id="u1", limit=10)
        assert {r.id for r in loaded} == {"m1", "m3"}

    def test_batch_load_enforces_user_boundary(self, repo, reader):
        repo.save_memory(_record("m1", user_id="u1"))
        repo.save_memory(_record("m2", user_id="other"))
        loaded = reader.load_memories_by_ids(("m1", "m2"), user_id="u1", limit=10)
        assert {r.id for r in loaded} == {"m1"}

    def test_batch_load_respects_limit(self, repo, reader):
        for i in range(5):
            repo.save_memory(_record(f"m{i}"))
        loaded = reader.load_memories_by_ids(
            tuple(f"m{i}" for i in range(5)), user_id="u1", limit=2
        )
        assert len(loaded) <= 2

    def test_batch_load_ignores_missing_ids(self, repo, reader):
        repo.save_memory(_record("m1"))
        loaded = reader.load_memories_by_ids(("m1", "ghost"), user_id="u1", limit=10)
        assert {r.id for r in loaded} == {"m1"}


class TestQueryScopedMemories:
    def test_filters_by_scope_and_types(self, repo, reader):
        repo.save_memory(_record("f1", type_=MemoryType.FACT))
        repo.save_memory(_record("e1", type_=MemoryType.EVENT))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_scoped_memories(scope, types=(MemoryType.FACT,), limit=10)
        assert {r.id for r in loaded} == {"f1"}

    def test_bounded_by_limit(self, repo, reader):
        for i in range(5):
            repo.save_memory(_record(f"m{i}"))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_scoped_memories(scope, types=(), limit=3)
        assert len(loaded) <= 3

    def test_excludes_other_user(self, repo, reader):
        repo.save_memory(_record("m1", user_id="u1", agent_id=None, session_id=None))
        repo.save_memory(_record("m2", user_id="other", agent_id=None, session_id=None))
        scope = MemoryScope(user_id="u1")
        loaded = reader.query_scoped_memories(scope, types=(), limit=10)
        assert {r.id for r in loaded} == {"m1"}

    def test_include_history_brings_superseded(self, repo, reader):
        repo.save_memory(_record("m1", validity=Validity.SUPERSEDED))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        assert reader.query_scoped_memories(scope, types=(), limit=10) == []
        with_history = reader.query_scoped_memories(scope, types=(), include_history=True, limit=10)
        assert {r.id for r in with_history} == {"m1"}


class TestQuerySupersededMemories:
    def test_returns_only_historical_versions(self, repo, reader):
        repo.save_memory(_record("cur", validity=Validity.ACTIVE))
        repo.save_memory(_record("old", validity=Validity.SUPERSEDED))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_superseded_memories(scope, types=(), limit=10)
        assert {r.id for r in loaded} == {"old"}

    def test_bounded_by_limit(self, repo, reader):
        for i in range(4):
            repo.save_memory(_record(f"h{i}", validity=Validity.SUPERSEDED))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_superseded_memories(scope, types=(), limit=2)
        assert len(loaded) <= 2


class TestQueryTemporalMemories:
    def test_valid_at_point_in_time(self, repo, reader):
        repo.save_memory(_record("early", valid_from=datetime(2026, 1, 1, tzinfo=UTC)))
        repo.save_memory(_record("late", valid_from=datetime(2026, 6, 1, tzinfo=UTC)))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_temporal_memories(
            scope, valid_at=datetime(2026, 3, 1, tzinfo=UTC), limit=10
        )
        assert {r.id for r in loaded} == {"early"}

    def test_created_before(self, repo, reader):
        repo.save_memory(_record("old", created_at=datetime(2026, 1, 1, tzinfo=UTC)))
        repo.save_memory(_record("new", created_at=datetime(2026, 6, 1, tzinfo=UTC)))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_temporal_memories(
            scope, created_before=datetime(2026, 3, 1, tzinfo=UTC), limit=10
        )
        assert {r.id for r in loaded} == {"old"}

    def test_bounded_by_limit(self, repo, reader):
        for i in range(5):
            repo.save_memory(_record(f"m{i}", created_at=datetime(2026, 1, i + 1, tzinfo=UTC)))
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.query_temporal_memories(scope, limit=2)
        assert len(loaded) <= 2


class TestGetRelationsBatch:
    def test_batch_relations_grouped_by_memory(self, repo, reader):
        repo.save_memory(_record("m1"))
        repo.save_memory(_record("m2"))
        _insert_relation(repo, "m1", "m2", RelationKind.SUPERSEDES)
        _insert_relation(repo, "m2", "m1", RelationKind.CORRECTS)
        grouped = reader.get_relations_batch(("m1", "m2"), user_id="u1")
        m1_rels = {r.relation for r in grouped["m1"]}
        m2_rels = {r.relation for r in grouped["m2"]}
        # Relations are bidirectional associations; both memories carry both.
        assert m1_rels == {RelationKind.SUPERSEDES, RelationKind.CORRECTS}
        assert m2_rels == {RelationKind.SUPERSEDES, RelationKind.CORRECTS}

    def test_rejects_cross_user_relation(self, repo, reader):
        repo.save_memory(_record("m1", user_id="u1"))
        repo.save_memory(_record("m2", user_id="other", agent_id=None, session_id=None))
        _insert_relation(repo, "m1", "m2", RelationKind.SUPERSEDES)
        grouped = reader.get_relations_batch(("m1",), user_id="u1")
        # m2 belongs to another user; the cross-user relation must be dropped.
        assert grouped["m1"] == []

    def test_unknown_ids_return_empty(self, reader):
        grouped = reader.get_relations_batch(("ghost",), user_id="u1")
        assert grouped == {"ghost": []}


class TestListUnsyncedCoverage:
    def test_returns_active_records_with_pending_index_op(self, repo, reader):
        repo.save_memory(_record("m1"))
        repo.save_memory(_record("m2"))
        repo.enqueue_index_operation("req-1", "m1", IndexOperationKind.UPSERT)
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.list_unsynced_coverage(
            scope, types=(MemoryType.FACT, MemoryType.EVENT), limit=10
        )
        assert {r.id for r in loaded} == {"m1"}

    def test_excludes_synced_records(self, repo, reader):
        repo.save_memory(_record("m1"))
        op_id = repo.enqueue_index_operation("req-1", "m1", IndexOperationKind.UPSERT)
        repo.mark_index_operation(op_id, IndexOperationStatus.SYNCED)
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.list_unsynced_coverage(scope, types=(MemoryType.FACT,), limit=10)
        assert loaded == []

    def test_bounded_by_limit(self, repo, reader):
        for i in range(4):
            repo.save_memory(_record(f"m{i}"))
            repo.enqueue_index_operation(f"req-{i}", f"m{i}", IndexOperationKind.UPSERT)
        scope = MemoryScope(user_id="u1", agent_id="a1", session_id="s1")
        loaded = reader.list_unsynced_coverage(scope, types=(MemoryType.FACT,), limit=2)
        assert len(loaded) <= 2


class TestRevalidateFinalState:
    def test_returns_current_state_for_ids(self, repo, reader):
        repo.save_memory(_record("m1"))
        loaded = reader.revalidate_final_state(("m1",), user_id="u1")
        assert {r.id for r in loaded} == {"m1"}
        assert loaded[0].validity is Validity.ACTIVE

    def test_reflects_state_change_since_snapshot(self, repo, reader):
        repo.save_memory(_record("m1"))
        repo.save_memory(_record("m2"))
        repo.transition(
            "m1",
            Validity.SUPERSEDED,
            _relation_record("m1", "m2"),
        )
        loaded = reader.revalidate_final_state(("m1",), user_id="u1")
        assert loaded[0].validity is Validity.SUPERSEDED

    def test_enforces_user_boundary(self, repo, reader):
        repo.save_memory(_record("m1", user_id="u1"))
        repo.save_memory(_record("m2", user_id="other"))
        loaded = reader.revalidate_final_state(("m1", "m2"), user_id="u1")
        assert {r.id for r in loaded} == {"m1"}


def _relation_record(from_id: str, to_id: str):
    from agents_memory.models import MemoryRelation

    return MemoryRelation(
        from_memory_id=from_id,
        to_memory_id=to_id,
        relation=RelationKind.SUPERSEDES,
    )

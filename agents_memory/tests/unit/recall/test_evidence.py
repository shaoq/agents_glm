"""Tests for EvidenceResolver: temporal roles and explicit relation grouping
(task 6.1-6.4).
"""

from datetime import UTC, datetime

import pytest

from agents_memory.models import MemoryRecord, MemoryScope, MemoryType, Validity
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.evidence import EvidenceResolver
from agents_memory.recall.models import EvidenceRole, ScoredCandidate
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.repository import MemoryRepository


def _scored(
    memory_id: str,
    *,
    user_id: str = "u1",
    validity: Validity = Validity.ACTIVE,
    valid_from: datetime | None = None,
    utility: float = 0.8,
) -> ScoredCandidate:
    record = MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id, agent_id="a1", session_id="s1"),
        type=MemoryType.FACT,
        content=memory_id,
        importance=5,
        confidence=0.8,
        validity=validity,
        valid_from=valid_from,
    )
    return ScoredCandidate(memory_id=memory_id, record=record, utility=utility)


def _insert_relation(repo, from_id, to_id, relation):
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
def resolver(repo) -> EvidenceResolver:
    return EvidenceResolver(RecallReadRepository(repo.path))


def _resolve(resolver, *scored, user_id="u1"):
    from agents_memory.recall.models import RecallRequest

    request = RecallRequest(user_id=user_id, agent_id="a1", session_id="s1", query="q")
    return resolver.resolve(request, tuple(scored), RecallDiagnostics())


class TestTemporalRoles:
    def test_active_candidate_is_current(self, resolver):
        repo_record = _scored("m1", validity=Validity.ACTIVE)
        groups = _resolve(resolver, repo_record)
        assert len(groups) == 1
        assert groups[0].primary.role is EvidenceRole.CURRENT

    def test_superseded_candidate_is_historical(self, resolver):
        scored = _scored("m1", validity=Validity.SUPERSEDED)
        groups = _resolve(resolver, scored)
        assert groups[0].primary.role is EvidenceRole.HISTORICAL


class TestExplicitRelationGrouping:
    def test_supersedes_chain_groups_active_with_history(self, repo, resolver):
        from agents_memory.models import RelationKind

        current = _scored("cur", validity=Validity.ACTIVE)
        old = _scored("old", validity=Validity.SUPERSEDED)
        repo.save_memory(current.record)
        repo.save_memory(old.record)
        _insert_relation(repo, "cur", "old", RelationKind.SUPERSEDES)
        groups = _resolve(resolver, current, old)
        assert len(groups) == 1
        group = groups[0]
        assert group.primary.memory_id == "cur"
        assert group.primary.role is EvidenceRole.CURRENT
        historical_ids = {h.memory_id for h in group.historical}
        assert historical_ids == {"old"}

    def test_corrects_chain_groups_correction_with_corrected(self, repo, resolver):
        from agents_memory.models import RelationKind

        correction = _scored("fix", validity=Validity.ACTIVE)
        wrong = _scored("wrong", validity=Validity.SUPERSEDED)
        repo.save_memory(correction.record)
        repo.save_memory(wrong.record)
        _insert_relation(repo, "fix", "wrong", RelationKind.CORRECTS)
        groups = _resolve(resolver, correction, wrong)
        assert len(groups) == 1
        assert groups[0].primary.memory_id == "fix"

    def test_cycle_does_not_loop(self, repo, resolver):
        from agents_memory.models import RelationKind

        a = _scored("a", validity=Validity.ACTIVE)
        b = _scored("b", validity=Validity.SUPERSEDED)
        repo.save_memory(a.record)
        repo.save_memory(b.record)
        _insert_relation(repo, "a", "b", RelationKind.SUPERSEDES)
        _insert_relation(repo, "b", "a", RelationKind.SUPERSEDES)
        groups = _resolve(resolver, a, b)
        # Both end up in exactly one group; no infinite loop.
        assert len(groups) == 1
        ids = {groups[0].primary.memory_id, *(h.memory_id for h in groups[0].historical)}
        assert ids == {"a", "b"}

    def test_standalone_candidates_form_separate_groups(self, resolver):
        groups = _resolve(
            resolver,
            _scored("m1"),
            _scored("m2"),
            _scored("m3"),
        )
        assert len(groups) == 3
        assert {g.primary.memory_id for g in groups} == {"m1", "m2", "m3"}


class TestCrossUserRelationRejection:
    def test_cross_user_relation_not_grouped(self, repo, resolver):
        from agents_memory.models import RelationKind

        mine = _scored("mine", user_id="u1", validity=Validity.ACTIVE)
        repo.save_memory(mine.record)
        # Insert a relation to a record owned by another user; the repository's
        # get_relations_batch must drop it, so the candidates stay separate.
        other_record = MemoryRecord(
            id="theirs",
            scope=MemoryScope(user_id="other", agent_id="x", session_id="y"),
            type=MemoryType.FACT,
            content="x",
            importance=5,
            confidence=0.8,
        )
        repo.save_memory(other_record)
        _insert_relation(repo, "mine", "theirs", RelationKind.SUPERSEDES)
        groups = _resolve(resolver, mine)
        assert len(groups) == 1
        assert groups[0].primary.memory_id == "mine"
        assert groups[0].historical == ()

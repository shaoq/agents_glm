"""Tests for assess_eligibility and EligibilityFilter (task 4.7/4.8)."""

from datetime import UTC, datetime

import pytest

from agents_memory.models import MemoryRecord, MemoryScope, MemoryType, Validity
from agents_memory.recall.filtering import EligibilityFilter, assess_eligibility
from agents_memory.recall.models import (
    HitSignal,
    RecallLane,
    RecallRequest,
    RejectionReason,
    RetrievedCandidate,
    TemporalIntent,
    TemporalAnchor,
)
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.repository import MemoryRepository


def _record(
    memory_id: str = "m1",
    *,
    user_id: str = "u1",
    validity: Validity = Validity.ACTIVE,
    type_: MemoryType = MemoryType.FACT,
    content: str = "c",
    valid_from: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id, agent_id="a1", session_id="s1"),
        type=type_,
        content=content,
        importance=5,
        confidence=0.8,
        validity=validity,
        valid_from=valid_from,
    )


def _request(**kwargs) -> RecallRequest:
    defaults: dict = {"user_id": "u1", "agent_id": "a1", "session_id": "s1", "query": "q"}
    defaults.update(kwargs)
    return RecallRequest(**defaults)


class TestAssessEligibility:
    def test_active_record_passes(self):
        assert assess_eligibility(_request(), _record()) is None

    def test_wrong_user_rejected(self):
        record = _record(user_id="other")
        assert assess_eligibility(_request(), record) is RejectionReason.WRONG_USER

    def test_superseded_rejected_for_current_query(self):
        record = _record(validity=Validity.SUPERSEDED)
        assert assess_eligibility(_request(), record) is RejectionReason.INVALID_STATE

    def test_superseded_kept_for_historical_query(self):
        record = _record(validity=Validity.SUPERSEDED)
        request = _request(temporal_intent=TemporalIntent.POINT_IN_TIME)
        assert assess_eligibility(request, record) is None

    def test_retracted_always_rejected(self):
        record = _record(validity=Validity.RETRACTED)
        request = _request(temporal_intent=TemporalIntent.EVOLUTION)
        assert assess_eligibility(request, record) is RejectionReason.INVALID_STATE

    def test_explicit_type_violation(self):
        record = _record(type_=MemoryType.EVENT)
        request = _request(explicit_types=(MemoryType.FACT,))
        assert assess_eligibility(request, record) is RejectionReason.EXPLICIT_TYPE_VIOLATION

    def test_hard_time_violation(self):
        record = _record(valid_from=datetime(2026, 6, 1, tzinfo=UTC))
        anchor = TemporalAnchor(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        request = _request(explicit_time_range=anchor)
        assert assess_eligibility(request, record) is RejectionReason.HARD_TIME_VIOLATION

    def test_within_time_range_passes(self):
        record = _record(valid_from=datetime(2026, 2, 1, tzinfo=UTC))
        anchor = TemporalAnchor(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        request = _request(explicit_time_range=anchor)
        assert assess_eligibility(request, record) is None

    def test_corrupt_record_rejected(self):
        # Bypass MemoryRecord validation to simulate a corrupt row.
        record = MemoryRecord.model_construct(
            id="m1",
            scope=MemoryScope(user_id="u1"),
            type=MemoryType.FACT,
            content="",
            importance=5,
            confidence=0.8,
            validity=Validity.ACTIVE,
        )
        assert assess_eligibility(_request(), record) is RejectionReason.CORRUPT_RECORD


@pytest.fixture()
def repo(tmp_path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "test.db")


@pytest.fixture()
def filt(repo) -> EligibilityFilter:
    return EligibilityFilter(RecallReadRepository(repo.path))


def _hit(memory_id: str, lane: RecallLane = RecallLane.SESSION_CURRENT) -> HitSignal:
    return HitSignal(lane=lane, similarity=0.9)


class TestEligibilityFilter:
    def test_hydrates_and_passes_active(self, repo, filt):
        repo.save_memory(_record("m1"))
        candidates = (RetrievedCandidate(memory_id="m1", hits=(_hit("m1"),)),)
        eligible = filt.filter(_request(), candidates, _diag())
        assert [e.memory_id for e in eligible] == ["m1"]
        assert eligible[0].record.id == "m1"

    def test_missing_record_dropped(self, repo, filt):
        repo.save_memory(_record("m1"))
        candidates = (
            RetrievedCandidate(memory_id="m1", hits=(_hit("m1"),)),
            RetrievedCandidate(memory_id="ghost", hits=(_hit("ghost"),)),
        )
        eligible = filt.filter(_request(), candidates, _diag())
        assert [e.memory_id for e in eligible] == ["m1"]

    def test_cross_user_candidate_dropped(self, repo, filt):
        repo.save_memory(_record("mine", user_id="u1"))
        repo.save_memory(
            MemoryRecord(
                id="theirs",
                scope=MemoryScope(user_id="other", agent_id="x", session_id="y"),
                type=MemoryType.FACT,
                content="x",
                importance=5,
                confidence=0.8,
            )
        )
        candidates = (
            RetrievedCandidate(memory_id="mine", hits=(_hit("mine"),)),
            RetrievedCandidate(memory_id="theirs", hits=(_hit("theirs"),)),
        )
        eligible = filt.filter(_request(), candidates, _diag())
        assert [e.memory_id for e in eligible] == ["mine"]

    def test_merges_hits_for_same_memory(self, repo, filt):
        repo.save_memory(_record("m1"))
        candidates = (
            RetrievedCandidate(
                memory_id="m1",
                hits=(_hit("m1", RecallLane.SESSION_CURRENT),),
            ),
            RetrievedCandidate(
                memory_id="m1",
                hits=(_hit("m1", RecallLane.AGENT_HISTORY),),
            ),
        )
        eligible = filt.filter(_request(), candidates, _diag())
        assert len(eligible) == 1
        lanes = {h.lane for h in eligible[0].hits}
        assert lanes == {RecallLane.SESSION_CURRENT, RecallLane.AGENT_HISTORY}

    def test_superseded_rejected_for_current_in_filter(self, repo, filt):
        repo.save_memory(_record("old", validity=Validity.SUPERSEDED))
        candidates = (RetrievedCandidate(memory_id="old", hits=(_hit("old"),)),)
        eligible = filt.filter(_request(), candidates, _diag())
        assert eligible == ()

    def test_records_rejection_in_diagnostic_mode(self, repo, filt):
        repo.save_memory(_record("old", validity=Validity.SUPERSEDED))
        candidates = (RetrievedCandidate(memory_id="old", hits=(_hit("old"),)),)
        diag = _diag()
        filt.filter(_request(diagnostic=True), candidates, diag)
        assert any("invalid_state" in note for note in diag.notes)


def _diag():
    from agents_memory.recall.diagnostics import RecallDiagnostics

    return RecallDiagnostics()

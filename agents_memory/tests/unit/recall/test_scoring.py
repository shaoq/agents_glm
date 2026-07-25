"""Tests for utility scoring: components, combination, LLM batch review,
non-monopolization invariants and fallback (task 5.1-5.6).
"""

import json
from types import SimpleNamespace

from agents_memory.models import MemoryRecord, MemoryScope, MemoryType
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    DegradationCode,
    EligibleCandidate,
    EvidenceRole,
    HitSignal,
    RecallLane,
    RecallRequest,
)
from agents_memory.recall.scoring import (
    DEFAULT_WEIGHTS,
    GLMBatchReviewer,
    UtilityScorer,
    base_components,
    combine,
)


def _candidate(
    memory_id: str = "m1",
    *,
    similarity: float = 0.9,
    lane: RecallLane = RecallLane.SESSION_CURRENT,
    importance: int = 5,
    confidence: float = 0.8,
) -> EligibleCandidate:
    record = MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
        type=MemoryType.FACT,
        content=memory_id,
        importance=importance,
        confidence=confidence,
    )
    return EligibleCandidate(
        memory_id=memory_id,
        record=record,
        hits=(HitSignal(lane=lane, similarity=similarity),),
    )


def _request(**kwargs) -> RecallRequest:
    defaults: dict = {"user_id": "u1", "agent_id": "a1", "session_id": "s1", "query": "q"}
    defaults.update(kwargs)
    return RecallRequest(**defaults)


class _FakeClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error

    @property
    def chat(self) -> "_FakeClient":
        return self

    @property
    def completions(self) -> "_FakeClient":
        return self

    def create(self, **kwargs):  # noqa: ARG002
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class TestBaseComponents:
    def test_components_are_normalized(self):
        components = base_components(_candidate(), _request())
        assert set(components) == set(DEFAULT_WEIGHTS)
        for value in components.values():
            assert 0.0 <= value <= 1.0

    def test_semantic_relevance_uses_best_hit(self):
        candidate = _candidate(similarity=0.7)
        assert base_components(candidate, _request())["semantic_relevance"] == 0.7


class TestCombine:
    def test_combine_is_weighted_sum(self):
        components = {name: 1.0 for name in DEFAULT_WEIGHTS}
        assert combine(components, DEFAULT_WEIGHTS) == 1.0

    def test_combine_zero_when_all_zero(self):
        components = {name: 0.0 for name in DEFAULT_WEIGHTS}
        assert combine(components, DEFAULT_WEIGHTS) == 0.0


class TestUtilityScorerDeterministic:
    def test_no_reviewer_uses_heuristic_task_contribution(self):
        scorer = UtilityScorer()
        scored = scorer.score(_request(), (_candidate(similarity=0.6),), RecallDiagnostics())
        assert len(scored) == 1
        assert scored[0].scoring_fallback is False
        components = {c.name: c.value for c in scored[0].components}
        assert components["task_contribution"] == 0.6

    def test_empty_candidates_returns_empty(self):
        assert UtilityScorer().score(_request(), (), RecallDiagnostics()) == ()


class TestLLMBatchReview:
    def _review_json(self, memory_id: str, tc: float, role: str) -> str:
        return json.dumps(
            {"reviews": [{"memory_id": memory_id, "task_contribution": tc, "role": role}]}
        )

    def test_review_overrides_task_contribution(self):
        reviewer = GLMBatchReviewer(
            client=_FakeClient(content=self._review_json("m1", 0.2, "supporting")),
            model="m",
        )
        scorer = UtilityScorer(reviewer=reviewer)
        scored = scorer.score(_request(), (_candidate(),), RecallDiagnostics())
        components = {c.name: c.value for c in scored[0].components}
        assert components["task_contribution"] == 0.2
        assert scored[0].provisional_role is EvidenceRole.SUPPORTING

    def test_low_task_contribution_lowers_utility_vs_high(self):
        candidate = _candidate(similarity=0.9, importance=9)

        def _score(tc: float) -> float:
            reviewer = GLMBatchReviewer(
                client=_FakeClient(content=self._review_json("m1", tc, "current")),
                model="m",
            )
            scored = UtilityScorer(reviewer=reviewer).score(
                _request(), (candidate,), RecallDiagnostics()
            )
            return scored[0].utility

        assert _score(0.1) < _score(0.9)

    def test_high_similarity_does_not_override_low_task_contribution(self):
        candidate = _candidate(similarity=0.95, importance=9)
        reviewer_low = GLMBatchReviewer(
            client=_FakeClient(content=self._review_json("m1", 0.0, "current")),
            model="m",
        )
        scored_low = UtilityScorer(reviewer=reviewer_low).score(
            _request(), (candidate,), RecallDiagnostics()
        )
        # Even with maxed similarity/importance, zero task contribution caps utility.
        assert scored_low[0].utility < 0.7

    def test_llm_failure_falls_back_to_deterministic(self):
        reviewer = GLMBatchReviewer(client=_FakeClient(error=TimeoutError("slow")), model="m")
        diag = RecallDiagnostics()
        scored = UtilityScorer(reviewer=reviewer).score(
            _request(), (_candidate(similarity=0.6),), diag
        )
        assert scored[0].scoring_fallback is True
        assert DegradationCode.SCORING_FALLBACK in diag.degradations
        components = {c.name: c.value for c in scored[0].components}
        assert components["task_contribution"] == 0.6  # heuristic preserved

    def test_malformed_output_falls_back(self):
        reviewer = GLMBatchReviewer(client=_FakeClient(content="not json"), model="m")
        diag = RecallDiagnostics()
        scored = UtilityScorer(reviewer=reviewer).score(_request(), (_candidate(),), diag)
        assert scored[0].scoring_fallback is True
        assert DegradationCode.SCORING_FALLBACK in diag.degradations

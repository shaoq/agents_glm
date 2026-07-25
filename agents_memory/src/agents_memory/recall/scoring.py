"""Explainable memory utility scoring.

Two-level scoring (design 8):
1. Deterministic base components for every eligible candidate: semantic
   relevance, task contribution (heuristic proxy), temporal fit, scope
   proximity, trust, hit robustness and a bounded importance term.
2. An optional LLM batch review that overrides task contribution and assigns
   a provisional evidence role for the top-N candidates.

The final utility is a transparent weighted sum of normalized components; no
single soft signal (and no opaque LLM total) can dominate. A high similarity
or importance cannot override a low task contribution. On LLM failure the
scorer keeps the deterministic base score and records ``scoring_fallback``.

Reference: design 8 (utility scoring).
"""

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    DegradationCode,
    EligibleCandidate,
    EvidenceRole,
    RecallLane,
    RecallRequest,
    ScoreComponent,
    ScoredCandidate,
    TemporalIntent,
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "semantic_relevance": 0.2,
    "task_contribution": 0.3,
    "temporal_fit": 0.1,
    "scope_proximity": 0.1,
    "trust": 0.1,
    "hit_robustness": 0.1,
    "bounded_importance": 0.1,
}

_SCOPE_PROXIMITY: dict[RecallLane, float] = {
    RecallLane.SESSION_CURRENT: 1.0,
    RecallLane.AGENT_HISTORY: 0.6,
    RecallLane.USER_SHARED: 0.3,
}


@dataclass(frozen=True)
class ScorerConfig:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    llm_review_top_n: int = 10


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _semantic_relevance(candidate: EligibleCandidate) -> float:
    sims = [h.similarity for h in candidate.hits if h.similarity is not None]
    return max(sims) if sims else 0.0


def _scope_proximity(candidate: EligibleCandidate) -> float:
    if not candidate.hits:
        return 0.0
    return max(_SCOPE_PROXIMITY.get(h.lane, 0.0) for h in candidate.hits)


def _temporal_fit(candidate: EligibleCandidate, request: RecallRequest) -> float:
    need = request.temporal_intent
    if need is None or need is TemporalIntent.CURRENT_STATE:
        return 0.5
    return 0.8 if candidate.record.valid_from is not None else 0.3


def _hit_robustness(candidate: EligibleCandidate) -> float:
    return min(1.0, len(candidate.hits) / 3.0)


def _bounded_importance(candidate: EligibleCandidate) -> float:
    return candidate.record.importance / 10.0


def _trust(candidate: EligibleCandidate) -> float:
    return candidate.record.confidence


def base_components(candidate: EligibleCandidate, request: RecallRequest) -> dict[str, float]:
    """Deterministic, normalized components in [0, 1].

    ``task_contribution`` is a heuristic proxy (semantic relevance) here; the
    LLM batch review overrides it when available.
    """

    semantic = _semantic_relevance(candidate)
    return {
        "semantic_relevance": semantic,
        "task_contribution": semantic,
        "temporal_fit": _temporal_fit(candidate, request),
        "scope_proximity": _scope_proximity(candidate),
        "trust": _trust(candidate),
        "hit_robustness": _hit_robustness(candidate),
        "bounded_importance": _bounded_importance(candidate),
    }


def combine(components: dict[str, float], weights: dict[str, float]) -> float:
    return sum(weights.get(name, 0.0) * value for name, value in components.items())


class _ReviewItem(BaseModel):
    memory_id: str
    task_contribution: float = 0.5
    role: str | None = None


class _BatchReviewOutput(BaseModel):
    reviews: list[_ReviewItem] = []


class GLMBatchReviewer:
    """Optional LLM batch reviewer overriding task contribution and role."""

    def __init__(self, *, client: Any, model: str, top_n: int = 10) -> None:
        self.client = client
        self.model = model
        self.top_n = top_n

    def review(
        self,
        request: RecallRequest,
        candidates: tuple[EligibleCandidate, ...],
        diag: RecallDiagnostics,
    ) -> dict[str, tuple[float, EvidenceRole | None]]:
        top = candidates[: self.top_n]
        if not top:
            return {}
        try:
            output = self._invoke(request, top)
        except (ValidationError, ValueError, TypeError, IndexError, KeyError) as exc:
            diag.degrade(DegradationCode.SCORING_FALLBACK, f"parse: {type(exc).__name__}")
            return {}
        except Exception as exc:  # noqa: BLE001 (any LLM failure is recoverable)
            diag.degrade(DegradationCode.SCORING_FALLBACK, type(exc).__name__)
            return {}
        result: dict[str, tuple[float, EvidenceRole | None]] = {}
        for item in output.reviews:
            role = None
            if item.role:
                try:
                    role = EvidenceRole(item.role)
                except ValueError:
                    role = None
            result[item.memory_id] = (_clamp(item.task_contribution), role)
        return result

    def _invoke(
        self,
        request: RecallRequest,
        candidates: tuple[EligibleCandidate, ...],
    ) -> _BatchReviewOutput:
        payload = {
            "query": request.query,
            "purpose": request.purpose,
            "candidates": [
                {"memory_id": c.memory_id, "content": c.record.content} for c in candidates
            ],
        }
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Review each candidate memory for how much it contributes "
                        "to the query. Return ONLY JSON: "
                        '{"reviews":[{"memory_id":"...","task_contribution":0.0,'
                        '"role":"current|historical|supporting|conflicting|independent"}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
        )
        content = response.choices[0].message.content
        return _BatchReviewOutput.model_validate_json(content)


class UtilityScorer:
    """Orchestrates deterministic base scoring and optional LLM review."""

    def __init__(
        self,
        config: ScorerConfig | None = None,
        reviewer: GLMBatchReviewer | None = None,
    ) -> None:
        self.config = config or ScorerConfig()
        self.reviewer = reviewer

    def score(
        self,
        request: RecallRequest,
        candidates: tuple[EligibleCandidate, ...],
        diag: RecallDiagnostics,
    ) -> tuple[ScoredCandidate, ...]:
        if not candidates:
            return ()
        reviews: dict[str, tuple[float, EvidenceRole | None]] = {}
        if self.reviewer is not None:
            reviews = self.reviewer.review(request, candidates, diag)
        scoring_fallback = self.reviewer is not None and not reviews
        scored: list[ScoredCandidate] = []
        for candidate in candidates:
            components = base_components(candidate, request)
            role: EvidenceRole | None = None
            if candidate.memory_id in reviews:
                task_contribution, role = reviews[candidate.memory_id]
                components["task_contribution"] = task_contribution
            utility = combine(components, self.config.weights)
            scored.append(
                ScoredCandidate(
                    memory_id=candidate.memory_id,
                    record=candidate.record,
                    hits=candidate.hits,
                    components=tuple(
                        ScoreComponent(name=name, value=value) for name, value in components.items()
                    ),
                    utility=utility,
                    confidence=components["task_contribution"],
                    scoring_fallback=scoring_fallback,
                    provisional_role=role,
                )
            )
        return tuple(scored)

"""Strongly-typed ANALYZE sufficiency review models (analyze-sufficiency-feedback
Decision 2 / tasks 1.2-1.4).

The ANALYZE phase must decide — before WRITING — whether the current EvidenceSet
supports the candidate Analysis. Both the deterministic L0 funnel (zero required
evidence) and the L1 semantic reviewer emit the SAME typed verdict, so phase
acceptance never inspects a free-text ``reason`` to pick a branch.

These frozen value objects carry strict field invariants enforced at
construction. Provider output that violates them raises
:class:`SufficiencyValidationError` (an invalid response, mapped to
``UPSTREAM_ERROR`` by the handler) and never alters Run, Plan, Task or accepted
Analysis state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agents_orchestration.domain.enums import ReviewSource, SufficiencyVerdict
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.orchestration.proposals import ReplanProposal
from agents_orchestration.orchestration.report import AnalysisArtifact

# Fixed, testable ceilings for untrusted model text carried in review fields.
# A reviewer must not funnel arbitrary-size raw output through these fields.
GAP_HINT_MAX_LEN = 500
RATIONALE_MAX_LEN = 1000


class SufficiencyValidationError(ValueError):
    """A typed sufficiency model violated a field invariant (task 1.3).

    Raised at construction so invalid provider output fails fast; the
    AnalysisPhaseHandler maps it to an ``UPSTREAM_ERROR`` IDLE.
    """


@dataclass(frozen=True)
class SufficiencyReview:
    """One typed sufficiency judgement from the L0 or L1 funnel (task 1.2).

    Invariants (task 1.3):
    - ``RESEARCH_GAP`` MUST carry a ``gap_hint`` that is non-empty after
      whitespace stripping and no longer than ``GAP_HINT_MAX_LEN``.
    - ``SUFFICIENT`` / ``CONFLICT`` MUST NOT carry a ``gap_hint`` (it is None).
    - ``rationale`` is always present, stripped and length-bounded.
    """

    verdict: SufficiencyVerdict
    source: ReviewSource
    rationale: str
    gap_hint: str | None = None

    def __post_init__(self) -> None:
        rationale = self.rationale.strip()
        if not rationale:
            raise SufficiencyValidationError("rationale must be non-empty")
        if len(rationale) > RATIONALE_MAX_LEN:
            raise SufficiencyValidationError(
                f"rationale exceeds {RATIONALE_MAX_LEN} characters"
            )
        # Frozen dataclass: bypass the field via object.__setattr__ to normalize.
        object.__setattr__(self, "rationale", rationale)

        if self.verdict is SufficiencyVerdict.RESEARCH_GAP:
            if self.gap_hint is None:
                raise SufficiencyValidationError("research_gap requires a gap_hint")
            hint = self.gap_hint.strip()
            if not hint:
                raise SufficiencyValidationError("gap_hint must be non-empty")
            if len(hint) > GAP_HINT_MAX_LEN:
                raise SufficiencyValidationError(
                    f"gap_hint exceeds {GAP_HINT_MAX_LEN} characters"
                )
            object.__setattr__(self, "gap_hint", hint)
        elif self.gap_hint is not None:
            raise SufficiencyValidationError(
                f"{self.verdict.value} must not carry a gap_hint"
            )


@dataclass(frozen=True)
class SanitizedGap:
    """A cleaned, length-bounded research gap with stable correlation ids (task 2.1).

    ``gap_id`` identifies the gap across a replan loop; ``focus_hash`` binds the
    gap to the objective it was filed against, so ``PLAN_REPLANNED`` and the
    accepted ANALYZE Stage can correlate gap → Plan v+1 → new Evidence → later
    sufficient (task 9.1) without free-text matching.
    """

    cleaned: str
    gap_id: str
    focus_hash: str


@dataclass(frozen=True)
class FocusedReplan:
    """A plan-scoped focused replan proposal plus its sanitized gap context.

    The ``proposal`` is the bounded :class:`ReplanProposal` consumed by the atomic
    ``replan_and_transition`` (task 4.1); ``gap`` carries the correlation ids that
    the coordinator records in ``PLAN_REPLANNED`` and the ANALYZE Stage.
    """

    proposal: ReplanProposal
    gap: SanitizedGap


@dataclass(frozen=True)
class AnalysisSufficiencyOutcome:
    """The ANALYZE phase outcome consumed by ``AnalysisPhaseHandler.accept``.

    ``proposal`` on the :class:`PhaseOutcome` carries exactly one of these rather
    than a bare Analysis/report object, so the accept branch is selected from the
    typed verdict and source — never from reason-string parsing (task 1.3).

    Invariants:
    - L0 (``STRUCTURAL``) only emits ``RESEARCH_GAP`` with ``analysis is None``.
    - L1 (``SEMANTIC``) always carries a candidate ``analysis``.
    - ``focused_replan`` is only permitted on the ``RESEARCH_GAP`` branch.
    - ``source_evidence_hash`` binds the review to the exact reviewed EvidenceSet.
    """

    review: SufficiencyReview
    source_evidence_hash: str
    analysis: AnalysisArtifact | None = None
    focused_replan: FocusedReplan | None = None

    def __post_init__(self) -> None:
        review = self.review
        if not self.source_evidence_hash:
            raise SufficiencyValidationError("source_evidence_hash is required")

        if review.source is ReviewSource.STRUCTURAL:
            # L0 is the zero-required-evidence short-circuit: gap only, no analysis.
            if review.verdict is not SufficiencyVerdict.RESEARCH_GAP:
                raise SufficiencyValidationError("structural review must be a research_gap")
            if self.analysis is not None:
                raise SufficiencyValidationError(
                    "structural research_gap must not carry an analysis"
                )
        elif self.analysis is None:  # SEMANTIC (L1) always judges a candidate analysis.
            raise SufficiencyValidationError("semantic review requires an analysis")

        if (
            review.verdict is not SufficiencyVerdict.RESEARCH_GAP
            and self.focused_replan is not None
        ):
            raise SufficiencyValidationError(
                f"{review.verdict.value} must not carry a focused_replan"
            )

    @property
    def is_gap(self) -> bool:
        return self.review.verdict is SufficiencyVerdict.RESEARCH_GAP


def source_evidence_hash(evidence: EvidenceSet) -> str:
    """Deterministic SHA-256 over the reviewed EvidenceSet identity + content.

    Two reviews over the same independent evidence (ids + content refs/texts)
    produce the same hash, so a later accepted Stage can record which evidence a
    gap was resolved against (task 9.1). Order-independent.
    """

    items: list[tuple[str, str]] = []
    for ev in evidence.evidences:
        content = ev.content_ref or ev.content_text or ""
        items.append((ev.evidence_id, content))
    payload = json.dumps(
        {"run_id": evidence.run_id, "evidence": sorted(items)}, sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "GAP_HINT_MAX_LEN",
    "RATIONALE_MAX_LEN",
    "AnalysisSufficiencyOutcome",
    "FocusedReplan",
    "SanitizedGap",
    "SufficiencyReview",
    "SufficiencyValidationError",
    "source_evidence_hash",
]

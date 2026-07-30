"""Unit tests for typed ANALYZE sufficiency models (task 1.4)."""

from __future__ import annotations

import dataclasses

import pytest

from agents_orchestration.domain.enums import (
    BranchRole,
    CapabilityKind,
    ReviewSource,
    SufficiencyVerdict,
    WorkerRole,
)
from agents_orchestration.domain.evidence import Evidence, EvidenceSet, SourceIdentity, SourceKind
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.orchestration.proposals import ReplanProposal
from agents_orchestration.orchestration.report import AnalysisArtifact
from agents_orchestration.orchestration.sufficiency import (
    GAP_HINT_MAX_LEN,
    RATIONALE_MAX_LEN,
    AnalysisSufficiencyOutcome,
    SufficiencyReview,
    SufficiencyValidationError,
    source_evidence_hash,
)


def _analysis(run_id: str = "r1") -> AnalysisArtifact:
    return AnalysisArtifact(
        run_id=run_id, conclusions=("c1",), cited_evidence_ids=("e1",), open_questions=()
    )


def _focused_replan(run_id: str = "r1") -> ReplanProposal:
    return ReplanProposal(
        run_id=run_id,
        reason="gap",
        add_task_specs=(
            TaskSpec(
                task_id="research-2",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="more research",
                required_capabilities=(CapabilityKind.RAG_SEARCH,),
                branch_role=BranchRole.REQUIRED,
            ),
        ),
    )


def _evidence_set(evidence_id: str = "e1", content: str = "body") -> EvidenceSet:
    return EvidenceSet(
        run_id="r1",
        evidences=(
            Evidence(
                evidence_id=evidence_id,
                source=SourceIdentity(source_id=evidence_id, source_kind=SourceKind.RAG),
                content_text=content,
            ),
        ),
    )


# --- SufficiencyReview: legal verdicts ------------------------------------


@pytest.mark.unit
def test_sufficient_review_is_valid():
    review = SufficiencyReview(
        verdict=SufficiencyVerdict.SUFFICIENT,
        source=ReviewSource.SEMANTIC,
        rationale="evidence supports conclusions",
    )
    assert review.gap_hint is None
    assert review.rationale == "evidence supports conclusions"


@pytest.mark.unit
def test_conflict_review_is_valid_without_hint():
    review = SufficiencyReview(
        verdict=SufficiencyVerdict.CONFLICT,
        source=ReviewSource.SEMANTIC,
        rationale="conflicting evidence needs adjudication",
    )
    assert review.gap_hint is None


@pytest.mark.unit
def test_research_gap_review_is_valid_with_hint():
    review = SufficiencyReview(
        verdict=SufficiencyVerdict.RESEARCH_GAP,
        source=ReviewSource.SEMANTIC,
        rationale="missing coverage",
        gap_hint="need competitive data",
    )
    assert review.gap_hint == "need competitive data"


@pytest.mark.unit
def test_gap_hint_whitespace_is_stripped():
    review = SufficiencyReview(
        verdict=SufficiencyVerdict.RESEARCH_GAP,
        source=ReviewSource.SEMANTIC,
        rationale="x",
        gap_hint="   trimmed hint   ",
    )
    assert review.gap_hint == "trimmed hint"


# --- SufficiencyReview: invalid gap_hint combinations ---------------------


@pytest.mark.unit
def test_research_gap_without_hint_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.RESEARCH_GAP,
            source=ReviewSource.SEMANTIC,
            rationale="x",
        )


@pytest.mark.unit
def test_research_gap_blank_hint_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.RESEARCH_GAP,
            source=ReviewSource.SEMANTIC,
            rationale="x",
            gap_hint="   ",
        )


@pytest.mark.unit
def test_research_gap_overlong_hint_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.RESEARCH_GAP,
            source=ReviewSource.SEMANTIC,
            rationale="x",
            gap_hint="a" * (GAP_HINT_MAX_LEN + 1),
        )


@pytest.mark.unit
def test_sufficient_with_hint_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.SUFFICIENT,
            source=ReviewSource.SEMANTIC,
            rationale="x",
            gap_hint="should not be here",
        )


@pytest.mark.unit
def test_conflict_with_hint_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.CONFLICT,
            source=ReviewSource.SEMANTIC,
            rationale="x",
            gap_hint="should not be here",
        )


# --- SufficiencyReview: rationale invariants ------------------------------


@pytest.mark.unit
def test_empty_rationale_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.SUFFICIENT,
            source=ReviewSource.SEMANTIC,
            rationale="   ",
        )


@pytest.mark.unit
def test_overlong_rationale_fails():
    with pytest.raises(SufficiencyValidationError):
        SufficiencyReview(
            verdict=SufficiencyVerdict.SUFFICIENT,
            source=ReviewSource.SEMANTIC,
            rationale="a" * (RATIONALE_MAX_LEN + 1),
        )


# --- AnalysisSufficiencyOutcome: consistent composites --------------------


@pytest.mark.unit
def test_structural_gap_outcome_is_valid_without_analysis():
    outcome = AnalysisSufficiencyOutcome(
        review=SufficiencyReview(
            verdict=SufficiencyVerdict.RESEARCH_GAP,
            source=ReviewSource.STRUCTURAL,
            rationale="zero independent evidence",
            gap_hint="collect baseline evidence",
        ),
        source_evidence_hash="sha256:abc",
        analysis=None,
    )
    assert outcome.analysis is None
    assert outcome.is_gap is True


@pytest.mark.unit
def test_semantic_sufficient_outcome_is_valid():
    outcome = AnalysisSufficiencyOutcome(
        review=SufficiencyReview(
            verdict=SufficiencyVerdict.SUFFICIENT,
            source=ReviewSource.SEMANTIC,
            rationale="ok",
        ),
        source_evidence_hash="sha256:abc",
        analysis=_analysis(),
    )
    assert outcome.is_gap is False


@pytest.mark.unit
def test_semantic_gap_outcome_carries_analysis_and_replan():
    outcome = AnalysisSufficiencyOutcome(
        review=SufficiencyReview(
            verdict=SufficiencyVerdict.RESEARCH_GAP,
            source=ReviewSource.SEMANTIC,
            rationale="gap",
            gap_hint="more sources",
        ),
        source_evidence_hash="sha256:abc",
        analysis=_analysis(),
        focused_replan=_focused_replan(),
    )
    assert outcome.focused_replan is not None
    assert outcome.is_gap is True


# --- AnalysisSufficiencyOutcome: inconsistent composites ------------------


@pytest.mark.unit
def test_structural_outcome_with_analysis_fails():
    with pytest.raises(SufficiencyValidationError):
        AnalysisSufficiencyOutcome(
            review=SufficiencyReview(
                verdict=SufficiencyVerdict.RESEARCH_GAP,
                source=ReviewSource.STRUCTURAL,
                rationale="x",
                gap_hint="g",
            ),
            source_evidence_hash="sha256:abc",
            analysis=_analysis(),
        )


@pytest.mark.unit
def test_structural_non_gap_outcome_fails():
    with pytest.raises(SufficiencyValidationError):
        AnalysisSufficiencyOutcome(
            review=SufficiencyReview(
                verdict=SufficiencyVerdict.SUFFICIENT,
                source=ReviewSource.STRUCTURAL,
                rationale="x",
            ),
            source_evidence_hash="sha256:abc",
        )


@pytest.mark.unit
def test_semantic_outcome_without_analysis_fails():
    with pytest.raises(SufficiencyValidationError):
        AnalysisSufficiencyOutcome(
            review=SufficiencyReview(
                verdict=SufficiencyVerdict.SUFFICIENT,
                source=ReviewSource.SEMANTIC,
                rationale="x",
            ),
            source_evidence_hash="sha256:abc",
            analysis=None,
        )


@pytest.mark.unit
def test_focused_replan_on_sufficient_fails():
    with pytest.raises(SufficiencyValidationError):
        AnalysisSufficiencyOutcome(
            review=SufficiencyReview(
                verdict=SufficiencyVerdict.SUFFICIENT,
                source=ReviewSource.SEMANTIC,
                rationale="x",
            ),
            source_evidence_hash="sha256:abc",
            analysis=_analysis(),
            focused_replan=_focused_replan(),
        )


@pytest.mark.unit
def test_focused_replan_on_conflict_fails():
    with pytest.raises(SufficiencyValidationError):
        AnalysisSufficiencyOutcome(
            review=SufficiencyReview(
                verdict=SufficiencyVerdict.CONFLICT,
                source=ReviewSource.SEMANTIC,
                rationale="x",
            ),
            source_evidence_hash="sha256:abc",
            analysis=_analysis(),
            focused_replan=_focused_replan(),
        )


@pytest.mark.unit
def test_missing_source_evidence_hash_fails():
    with pytest.raises(SufficiencyValidationError):
        AnalysisSufficiencyOutcome(
            review=SufficiencyReview(
                verdict=SufficiencyVerdict.SUFFICIENT,
                source=ReviewSource.SEMANTIC,
                rationale="x",
            ),
            source_evidence_hash="",
            analysis=_analysis(),
        )


# --- immutability ---------------------------------------------------------


@pytest.mark.unit
def test_review_is_frozen():
    review = SufficiencyReview(
        verdict=SufficiencyVerdict.SUFFICIENT,
        source=ReviewSource.SEMANTIC,
        rationale="x",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        review.rationale = "mutated"  # type: ignore[misc]


# --- source_evidence_hash -------------------------------------------------


@pytest.mark.unit
def test_source_evidence_hash_is_deterministic_and_order_independent():
    ev_a = _evidence_set("e1", "body1")
    ev_b = EvidenceSet(
        run_id="r1",
        evidences=(
            Evidence(
                evidence_id="e2",
                source=SourceIdentity(source_id="e2", source_kind=SourceKind.RAG),
                content_text="body2",
            ),
            Evidence(
                evidence_id="e1",
                source=SourceIdentity(source_id="e1", source_kind=SourceKind.RAG),
                content_text="body1",
            ),
        ),
    )
    # Same membership in different order -> same hash; different membership -> different.
    assert source_evidence_hash(ev_b) == source_evidence_hash(ev_b)
    assert source_evidence_hash(ev_a) != source_evidence_hash(ev_b)
    assert source_evidence_hash(ev_a).startswith("sha256:")

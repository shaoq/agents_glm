"""Integration tests for analysis, report, review and finalization (Section 10)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import (
    CompletionState,
    ReviewVerdict,
    RunState,
    Sufficiency,
    TerminationReason,
)
from agents_orchestration.domain.evidence import (
    Degradation,
    Evidence,
    EvidenceSet,
    SourceIdentity,
    SourceKind,
)
from agents_orchestration.domain.goal import CompletionContract, CompletionCriterion, CriterionKind
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    CitationValidator,
    CompletionEvaluator,
    Finalizer,
    ReportBuilder,
    ReportContent,
    ReportSection,
    ReviewProposal,
    map_review_verdict,
)
from tests.integration.test_runtime import _seed


def _contract() -> CompletionContract:
    return CompletionContract(
        criteria=(
            CompletionCriterion(
                kind=CriterionKind.DELIVERABLE,
                description="report.md",
                deliverable_path="report.md",
            ),
            CompletionCriterion(
                kind=CriterionKind.EVIDENCE_SUFFICIENCY, description="enough evidence"
            ),
            CompletionCriterion(
                kind=CriterionKind.CITATION_INTEGRITY, description="citations valid"
            ),
        ),
        deliverable_paths=("report.md",),
    )


def _evidence_set(sufficiency=Sufficiency.SUFFICIENT, ids=("e1", "e2")) -> EvidenceSet:
    return EvidenceSet(
        run_id="r1",
        evidences=tuple(
            Evidence(
                evidence_id=eid,
                source=SourceIdentity(source_id=eid, source_kind=SourceKind.RAG, uri=f"u:{eid}"),
                content_text="passage",
            )
            for eid in ids
        ),
        sufficiency=sufficiency,
        independent_count=len(ids),
    )


def _report(cited=("e1", "e2")) -> ReportContent:
    return ReportContent(
        run_id="r1",
        title="Research Report",
        objective="answer X",
        sections=(ReportSection(title="Findings", body="body", cited_evidence_ids=cited),),
        conclusions=("X is true",),
        cited_evidence_ids=cited,
    )


# --- 10.2 ReportContent markdown / structured -------------------------------


@pytest.mark.integration
def test_report_content_renders_markdown_and_structured() -> None:
    report = _report()
    md = report.to_markdown()
    assert "# Research Report" in md and "## Findings" in md and "- X is true" in md
    structured = report.to_structured()
    assert structured["title"] == "Research Report" and structured["conclusions"] == ["X is true"]


# --- 10.6 Completion evaluation ---------------------------------------------


@pytest.mark.integration
def test_completion_evaluator_satisfied_when_deliverable_and_evidence_present() -> None:
    per, overall = CompletionEvaluator().evaluate(
        _contract(),
        evidence=_evidence_set(),
        deliverables_present={"report.md": True},
    )
    assert overall is CompletionState.SATISFIED
    assert per["report.md"] is CompletionState.SATISFIED


@pytest.mark.integration
def test_completion_evaluator_unsatisfied_when_deliverable_missing() -> None:
    _per, overall = CompletionEvaluator().evaluate(
        _contract(),
        evidence=_evidence_set(),
        deliverables_present={"report.md": False},
    )
    assert overall is CompletionState.UNSATISFIED


@pytest.mark.integration
def test_completion_evaluator_unknown_when_evidence_insufficient() -> None:
    _per, overall = CompletionEvaluator().evaluate(
        _contract(),
        evidence=_evidence_set(sufficiency=Sufficiency.UNKNOWN),
        deliverables_present={"report.md": True},
    )
    assert overall is CompletionState.UNKNOWN


# --- 10.8 Citation integrity ------------------------------------------------


@pytest.mark.integration
def test_citation_validator_flags_unknown_evidence_ids() -> None:
    report = _report(cited=("e1", "ghost"))
    invalid = CitationValidator().validate(report, evidence_ids={"e1", "e2"})
    assert invalid == ["ghost"]


# --- 10.3 / 10.5 Review proposals -------------------------------------------


@pytest.mark.integration
def test_review_verdict_maps_to_replan_or_gate() -> None:
    assert map_review_verdict(ReviewVerdict.RESEARCH_GAP) == "replan"
    assert map_review_verdict(ReviewVerdict.CONFLICT) == "gate"
    assert map_review_verdict(ReviewVerdict.PASS) == "none"
    proposal = ReviewProposal(verdict=ReviewVerdict.REVISE, reason="tighten")
    assert proposal.verdict is ReviewVerdict.REVISE


# --- 10.9 / 10.10 immutable artifacts + partial summary --------------------


@pytest.mark.integration
def test_report_builder_writes_three_content_addressed_artifacts(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    report = _report()
    analysis = AnalysisArtifact(run_id="r1", conclusions=("X is true",), cited_evidence_ids=("e1",))
    evidence = _evidence_set()
    degradations = (Degradation(flag="optional_lane_failed", reason="memory", fallback_used=True),)

    with backend.unit_of_work() as uow:
        artifacts = ReportBuilder().build(
            uow.artifacts,
            run_id="r1",
            report=report,
            analysis=analysis,
            completion_overall=CompletionState.UNSATISFIED,
            evidence=evidence,
            degradations=degradations,
            termination=TerminationReason.REQUIRED_EVIDENCE_MISSING,
            clock=fake_clock,
        )
        uow.commit()

    with backend.unit_of_work() as uow:
        md = uow.artifacts.read(artifacts.report_markdown)
        summary = uow.artifacts.read(artifacts.run_summary)
    assert b"# Research Report" in md
    assert b'"unmet": true' in summary
    assert b'"optional_lane_failed"' in summary


# --- 10.7 Finalizer compare-and-set terminal commit ------------------------


@pytest.mark.integration
def test_finalizer_commits_terminal_state_via_cas(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    report = _report()
    with backend.unit_of_work() as uow:
        artifacts = ReportBuilder().build(
            uow.artifacts,
            run_id="r1",
            report=report,
            analysis=AnalysisArtifact(run_id="r1"),
            completion_overall=CompletionState.SATISFIED,
            evidence=_evidence_set(),
            degradations=(),
            termination=None,
            clock=fake_clock,
        )
        uow.commit()
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        terminal, reason = Finalizer().finalize(
            uow,
            run,
            artifacts=artifacts,
            completion_overall=CompletionState.SATISFIED,
            clock=fake_clock,
            idgen=backend.idgen,
        )
        uow.commit()
    assert reason is TerminationReason.COMPLETED
    with backend.unit_of_work() as uow:
        assert uow.runs.get("r1").state is RunState.SUCCEEDED


# --- 10.11 report content cannot trigger write capabilities ----------------


@pytest.mark.integration
def test_report_module_has_no_capability_routing() -> None:
    """Report production is pure data/computation over artifacts+evidence: it must
    not import or reference any capability router / executor / ``invoke`` (10.11),
    so a report's recommendations can never trigger a write-side-effect capability."""

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "agents_orchestration"
        / "orchestration"
        / "report.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("CapabilityRouter", "TaskExecutor", "WorkerExecutor", ".invoke(", "adapter"):
        assert forbidden not in source, f"report.py references {forbidden}"


@pytest.mark.integration
async def test_report_production_writes_only_artifacts(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    report = _report()
    with backend.unit_of_work() as uow:
        artifacts = ReportBuilder().build(
            uow.artifacts,
            run_id="r1",
            report=report,
            analysis=AnalysisArtifact(run_id="r1"),
            completion_overall=CompletionState.SATISFIED,
            evidence=_evidence_set(),
            degradations=(),
            termination=None,
            clock=fake_clock,
        )
        uow.commit()
    # Three content-addressed artifacts, all verifiable — no capability involved.
    assert artifacts.report_markdown.content_hash.startswith("sha256:")
    assert artifacts.report_json.content_hash.startswith("sha256:")
    assert artifacts.run_summary.content_hash.startswith("sha256:")

"""Integration tests for Analyze/Write/Review/Finalize phases (Ch.7).

Analysis/Writing/Review are coordinator-owned phase ports called directly by
their handlers (remove-noop-phase-tasks): they no longer dispatch or gate on a
per-role Task, so these tests exercise the handlers without seeding one.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.coordination import AdvanceDisposition, PhaseId
from agents_orchestration.domain.enums import RunState
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
)
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.analysis_artifact import (
    SqliteAnalysisArtifactStore,
)
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.phases import (
    AnalysisPhaseHandler,
    FinalizePhaseHandler,
    ReviewPhaseHandler,
    WritingPhaseHandler,
)
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    ReportContent,
    ReviewProposal,
)
from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _analysis_store(backend) -> SqliteAnalysisArtifactStore:
    return SqliteAnalysisArtifactStore(SqliteArtifactStore(backend.conn, backend.artifact_dir))


def _seed_run(backend, state: RunState, plan_version: int = 1) -> Run:
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=state,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=plan_version,
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    return run


async def _evidence(run_id: str) -> EvidenceSet:
    return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=False)


# --- Analysis / Writing (direct phase ports) --------------------------------


@pytest.mark.integration
async def test_analysis_produces_artifact_to_writing(backend) -> None:
    run = _seed_run(backend, RunState.ANALYZING)

    async def analyst(run_id, evidence):
        return AnalysisArtifact(run_id=run_id, conclusions=("c1",))

    handler = AnalysisPhaseHandler(
        analyst, _evidence, _analysis_store(backend), clock=backend.clock, idgen=backend.idgen
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.WRITING


@pytest.mark.integration
async def test_analysis_provider_failure_degrades_to_idle(backend) -> None:
    """remove-noop-phase-tasks 6.2: a direct port provider failure degrades to
    IDLE without creating any Task/Attempt/Lease record."""

    run = _seed_run(backend, RunState.ANALYZING)

    async def failing_analyst(run_id, evidence):
        raise RuntimeError("analyst down")

    handler = AnalysisPhaseHandler(
        failing_analyst,
        _evidence,
        _analysis_store(backend),
        clock=backend.clock,
        idgen=backend.idgen,
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "analyze-provider-failed" in report.reason


@pytest.mark.integration
async def test_writing_produces_draft_to_reviewing(backend) -> None:
    run = _seed_run(backend, RunState.WRITING)

    async def writer(run_id, analysis):
        return ReportContent(run_id=run_id, title="T", objective="O")

    async def analysis_provider(run_id):
        return AnalysisArtifact(run_id=run_id)

    handler = WritingPhaseHandler(
        writer, analysis_provider, clock=backend.clock, idgen=backend.idgen
    )
    report = await RunCoordinator(backend, {PhaseId.WRITE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.REVIEWING


# --- Review (7.6-7.9) ------------------------------------------------------


def _review_handler(backend, reviewer) -> ReviewPhaseHandler:
    async def report_provider(run_id):
        return ReportContent(run_id=run_id, title="T", objective="O")

    return ReviewPhaseHandler(reviewer, report_provider, clock=backend.clock, idgen=backend.idgen)


@pytest.mark.integration
async def test_review_pass_advances_to_finalizing(backend) -> None:
    run = _seed_run(backend, RunState.REVIEWING)

    async def reviewer(run_id, report):
        from agents_orchestration.domain.enums import ReviewVerdict

        return ReviewProposal(verdict=ReviewVerdict.PASS, reason="ok")

    report = await RunCoordinator(
        backend, {PhaseId.REVIEW: _review_handler(backend, reviewer)}
    ).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.FINALIZING


@pytest.mark.integration
async def test_review_revise_increments_revision_counter_to_writing(backend) -> None:
    from agents_orchestration.domain.enums import ReviewVerdict

    run = _seed_run(backend, RunState.REVIEWING)

    async def reviewer(run_id, report):
        return ReviewProposal(verdict=ReviewVerdict.REVISE, reason="tighten")

    coord = RunCoordinator(backend, {PhaseId.REVIEW: _review_handler(backend, reviewer)})
    report = await coord.advance(run.run_id)
    assert report.to_state is RunState.WRITING
    with backend.unit_of_work() as uow:
        assert uow.runs.get(run.run_id).revision_count == 1  # monotonic (task 7.8)
        uow.commit()


@pytest.mark.integration
async def test_review_revise_exhausted_degrades(backend) -> None:
    from agents_orchestration.domain.enums import ReviewVerdict

    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.REVIEWING,
        policy=RunPolicy.from_limits(SystemLimits()),
        revision_count=5,
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()

    async def reviewer(run_id, report):
        return ReviewProposal(verdict=ReviewVerdict.REVISE, reason="again")

    report = await RunCoordinator(
        backend, {PhaseId.REVIEW: _review_handler(backend, reviewer)}
    ).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "revision-exhausted" in report.reason  # bounded loop (task 7.9)


# --- Finalize (7.10-7.13) --------------------------------------------------


@pytest.mark.integration
async def test_finalize_completes_with_report_artifacts(backend) -> None:
    run = _seed_run(backend, RunState.FINALIZING)
    contract = CompletionContract(
        criteria=(
            CompletionCriterion(
                kind=CriterionKind.DELIVERABLE,
                description="report.md",
                deliverable_path="report.md",
            ),
        ),
        deliverable_paths=("report.md",),
    )
    with backend.unit_of_work() as uow:
        uow.completion.save(run.run_id, contract)
        uow.commit()

    async def report_provider(run_id):
        return ReportContent(run_id=run_id, title="T", objective="O")

    async def analysis_provider(run_id):
        return AnalysisArtifact(run_id=run_id)

    async def deliverables_provider(run_id):
        return {"report.md": True}

    handler = FinalizePhaseHandler(
        report_provider=report_provider,
        analysis_provider=analysis_provider,
        evidence_provider=_evidence,
        deliverables_provider=deliverables_provider,
        clock=backend.clock,
        idgen=backend.idgen,
    )
    report = await RunCoordinator(backend, {PhaseId.FINALIZE: handler}).advance(run.run_id)
    assert report.to_state is RunState.SUCCEEDED
    with backend.unit_of_work() as uow:
        # report.md / report.json / run-summary.json artifacts persisted (task 7.12)
        assert len(uow.artifacts.list_all()) == 3
        uow.commit()

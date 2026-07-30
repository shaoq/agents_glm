"""ANALYZE L0/L1 sufficiency funnel + budget-exhaustion termination (tasks 5.6 / 6.3)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.coordination import AdvanceDisposition, PhaseId
from agents_orchestration.domain.enums import (
    CapabilityKind,
    RunState,
    SufficiencyVerdict,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.analysis_artifact import (
    SqliteAnalysisArtifactStore,
    accepted_analysis_ref,
)
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.focused_replan import FocusedReplanBuilder
from agents_orchestration.orchestration.phases import AnalysisPhaseHandler
from agents_orchestration.orchestration.planner import PlanValidator
from agents_orchestration.orchestration.sufficiency import SufficiencyValidationError
from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore
from tests.integration.test_focused_replan_transition import _to_analyzing
from tests.integration.test_replan_preserves import _seed_researching
from tests.support.deterministic import FakeSufficiencyReviewer

ALL_CAPS = frozenset(CapabilityKind)


def _store(backend) -> SqliteAnalysisArtifactStore:
    return SqliteAnalysisArtifactStore(SqliteArtifactStore(backend.conn, backend.artifact_dir))


async def _empty_evidence_required(run_id: str) -> EvidenceSet:
    return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=True)


async def _empty_evidence_optional(run_id: str) -> EvidenceSet:
    return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=False)


def _handler_with_evidence(backend, analyst, reviewer, evidence_provider) -> AnalysisPhaseHandler:
    return AnalysisPhaseHandler(
        analyst,
        evidence_provider,
        _store(backend),
        reviewer,
        FocusedReplanBuilder(ALL_CAPS, backend.idgen),
        PlanValidator(SystemLimits()),
        clock=backend.clock,
        idgen=backend.idgen,
    )


class _ExplodingModel:
    """A stand-in analyst/reviewer that fails if invoked."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, *args, **kwargs):  # analyst signature
        self.called = True
        raise AssertionError("model must not be called on the L0 short-circuit")


async def _ok_analyst(run_id, evidence):
    from agents_orchestration.orchestration.report import AnalysisArtifact

    return AnalysisArtifact(run_id=run_id, conclusions=("c1",), cited_evidence_ids=("e1",))


def _seed_analyzing(backend):
    return _to_analyzing(_seed_researching(backend), backend)


# --- L0 structural short-circuit (5.6) ------------------------------------


@pytest.mark.integration
async def test_l0_zero_evidence_short_circuits_without_model(backend) -> None:
    exploding = _ExplodingModel()
    reviewer = _ExplodingModel()
    run = _seed_analyzing(backend)
    handler = _handler_with_evidence(
        backend, exploding, reviewer, _empty_evidence_required
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert exploding.called is False  # analyst not invoked
    assert reviewer.called is False  # L1 reviewer not invoked
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.RESEARCHING  # focused replan
    with backend.unit_of_work() as uow:
        moved = uow.runs.get(run.run_id)
        uow.commit()
    assert moved.replan_count == 1
    assert moved.current_plan_version == 2


# --- L1 sufficient / conflict -> WRITING (5.6) ----------------------------


@pytest.mark.integration
async def test_l1_sufficient_accepts_analysis_and_enters_writing(backend) -> None:
    run = _seed_analyzing(backend)
    handler = _handler_with_evidence(
        backend,
        _ok_analyst,
        FakeSufficiencyReviewer(SufficiencyVerdict.SUFFICIENT),
        _empty_evidence_optional,
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.to_state is RunState.WRITING
    with backend.unit_of_work() as uow:
        moved = uow.runs.get(run.run_id)
        ref = accepted_analysis_ref(uow.stages, run.run_id, moved.current_plan_version)
        uow.commit()
    assert ref is not None  # accepted artifact recorded for the writer


@pytest.mark.integration
async def test_l1_conflict_accepts_analysis_without_consuming_replan_budget(backend) -> None:
    run = _seed_analyzing(backend)
    handler = _handler_with_evidence(
        backend,
        _ok_analyst,
        FakeSufficiencyReviewer(SufficiencyVerdict.CONFLICT),
        _empty_evidence_optional,
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.to_state is RunState.WRITING
    with backend.unit_of_work() as uow:
        moved = uow.runs.get(run.run_id)
        uow.commit()
    assert moved.replan_count == 0  # conflict does not consume replan budget


# --- L1 research gap -> focused replan (5.6) ------------------------------


@pytest.mark.integration
async def test_l1_research_gap_raises_focused_replan(backend) -> None:
    run = _seed_analyzing(backend)
    handler = _handler_with_evidence(
        backend,
        _ok_analyst,
        FakeSufficiencyReviewer(SufficiencyVerdict.RESEARCH_GAP, gap_hint="need pricing data"),
        _empty_evidence_optional,
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.to_state is RunState.RESEARCHING
    with backend.unit_of_work() as uow:
        moved = uow.runs.get(run.run_id)
        effects = [e.effect.value for e in uow.events.stream(run.run_id)]
        new_tasks = [
            t
            for t in uow.tasks.by_run(run.run_id, plan_version=2)
            if t.worker_role is WorkerRole.EVIDENCE_RESEARCHER and t.state is TaskState.PENDING
        ]
        uow.commit()
    assert moved.replan_count == 1
    assert "plan_replanned" in effects and "run_state_transition" in effects
    assert len(new_tasks) >= 1  # at least one new PENDING research task


# --- invalid reviewer structure / provider failure -> IDLE (5.5 / 5.6) ----


@pytest.mark.integration
async def test_invalid_review_structure_degrades_to_idle(backend) -> None:
    run = _seed_analyzing(backend)

    class _BadReviewer:
        async def review(self, run_id, analysis, evidence):
            raise SufficiencyValidationError("sufficient must not carry gap_hint")

    handler = _handler_with_evidence(backend, _ok_analyst, _BadReviewer(), _empty_evidence_optional)
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "analyze-invalid-review" in report.reason
    assert report.continue_immediately is False
    with backend.unit_of_work() as uow:
        moved = uow.runs.get(run.run_id)
        uow.commit()
    assert moved.state is RunState.ANALYZING  # unchanged
    assert moved.replan_count == 0


@pytest.mark.integration
async def test_provider_failure_degrades_to_idle_without_state_change(backend) -> None:
    run = _seed_analyzing(backend)

    async def failing_analyst(run_id, evidence):
        raise RuntimeError("analyst down")

    handler = _handler_with_evidence(
        backend, failing_analyst, FakeSufficiencyReviewer(), _empty_evidence_optional
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "analyze-provider-failed" in report.reason
    assert report.continue_immediately is False


# --- budget exhaustion -> deterministic TERMINAL (6.3) --------------------


@pytest.mark.integration
async def test_research_gap_budget_exhausted_terminates(backend) -> None:
    run = _seed_analyzing(backend)
    # Burn the replan budget.
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        exhausted = current.model_copy(update={"replan_count": current.policy.max_replans})
        uow.runs.save(exhausted, expected_version=current.state_version)
        uow.commit()
    handler = _handler_with_evidence(
        backend,
        _ok_analyst,
        FakeSufficiencyReviewer(SufficiencyVerdict.RESEARCH_GAP, gap_hint="more"),
        _empty_evidence_optional,
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL
    with backend.unit_of_work() as uow:
        moved = uow.runs.get(run.run_id)
        effects = [e.effect.value for e in uow.events.stream(run.run_id)]
        plan = uow.plans.current(run.run_id)
        uow.commit()
    assert moved.state is RunState.FAILED
    assert moved.termination is TerminationReason.REQUIRED_EVIDENCE_MISSING
    assert "run_terminated" in effects
    assert plan.version == 1  # no Plan v+1 created — termination, not replan


@pytest.mark.integration
async def test_l0_budget_exhausted_terminates_without_model(backend) -> None:
    exploding = _ExplodingModel()
    run = _seed_analyzing(backend)
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        uow.runs.save(
            current.model_copy(update={"replan_count": current.policy.max_replans}),
            expected_version=current.state_version,
        )
        uow.commit()
    handler = _handler_with_evidence(
        backend, exploding, _ExplodingModel(), _empty_evidence_required
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: handler}).advance(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL
    assert exploding.called is False  # provider invoked at most once (here: zero)

"""Integration tests for Goal and Planning phase handlers (Ch.5 tasks 5.1-5.10)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.coordination import AdvanceDisposition, PhaseId
from agents_orchestration.domain.enums import CapabilityKind, RunState, WorkerRole
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.phases import GoalPhaseHandler, PlanningPhaseHandler
from agents_orchestration.orchestration.proposals import (
    GoalClarificationProposal,
    GoalNormalizationOutcome,
    PlanProposal,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)
ALL_CAPS = frozenset(CapabilityKind)


def _seed(backend, state: RunState = RunState.NORMALIZING, plan_version=None) -> Run:
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


def _goal() -> GoalSpec:
    return GoalSpec(raw_input="g", objective="analyze X", deliverables=("report.md",))


def _contract() -> CompletionContract:
    return CompletionContract(
        criteria=(
            CompletionCriterion(
                kind=CriterionKind.DELIVERABLE,
                description="report.md",
                deliverable_path="report.md",
            ),
        ),
        deliverable_paths=("report.md",),
    )


def _valid_proposal(run_id: str) -> PlanProposal:
    return PlanProposal(
        run_id=run_id,
        plan_id="p1",
        task_specs=(
            TaskSpec(
                task_id="t1",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gather evidence",
                deliverable_path="report.md",
            ),
        ),
        deliverable_paths=("report.md",),
    )


class _Norm:
    def __init__(self, outcome: GoalNormalizationOutcome) -> None:
        self._o = outcome

    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
        return self._o


class _FailingNorm:
    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
        raise RuntimeError("boom")


class _Planner:
    def __init__(self, proposal: PlanProposal) -> None:
        self._p = proposal

    async def propose_plan(self, goal, completion, run_id: str) -> PlanProposal:
        return self._p


class _FailingPlanner:
    async def propose_plan(self, goal, completion, run_id: str) -> PlanProposal:
        raise RuntimeError("boom")


# --- Goal phase (5.2 / 5.3 / 5.4 / 5.5) ------------------------------------


@pytest.mark.integration
async def test_goal_clear_advances_to_planning_and_persists(backend) -> None:
    handler = GoalPhaseHandler(
        _Norm(GoalNormalizationOutcome(_goal(), _contract(), None)), backend.idgen
    )
    coord = RunCoordinator(backend, {PhaseId.GOAL: handler})
    run = _seed(backend, RunState.NORMALIZING)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.PLANNING
    with backend.unit_of_work() as uow:
        assert uow.goals.get(run.run_id) is not None
        assert uow.completion.get(run.run_id) is not None
        uow.commit()


@pytest.mark.integration
async def test_goal_ambiguous_opens_clarification_gate(backend) -> None:
    clarification = GoalClarificationProposal(
        run_id="x", ambiguities=("missing objective",), questions=("Clarify objective?",)
    )
    handler = GoalPhaseHandler(
        _Norm(GoalNormalizationOutcome(_goal(), _contract(), clarification)), backend.idgen
    )
    coord = RunCoordinator(backend, {PhaseId.GOAL: handler})
    run = _seed(backend, RunState.NORMALIZING)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.BLOCKED
    with backend.unit_of_work() as uow:
        gates = list(uow.gates.open_for_run(run.run_id))
        assert any(g.gate_type.value == "goal_clarification" for g in gates)
        uow.commit()


@pytest.mark.integration
async def test_goal_provider_failure_degrades_idle(backend) -> None:
    handler = GoalPhaseHandler(_FailingNorm(), backend.idgen)
    coord = RunCoordinator(backend, {PhaseId.GOAL: handler})
    run = _seed(backend, RunState.NORMALIZING)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "failed" in report.reason


# --- Planning phase (5.6 / 5.7 / 5.8 / 5.10) --------------------------------


def _seed_planning(backend) -> Run:
    run = _seed(backend, RunState.PLANNING)
    with backend.unit_of_work() as uow:
        uow.goals.save(run.run_id, _goal())
        uow.completion.save(run.run_id, _contract())
        uow.commit()
    return run


def _planner_handler(backend, planner) -> PlanningPhaseHandler:
    return PlanningPhaseHandler(
        planner,
        limits=SystemLimits(),
        allowed_capabilities=ALL_CAPS,
        clock=backend.clock,
        idgen=backend.idgen,
    )


@pytest.mark.integration
async def test_planning_valid_advances_to_researching(backend) -> None:
    run = _seed_planning(backend)
    coord = RunCoordinator(
        backend, {PhaseId.PLAN: _planner_handler(backend, _Planner(_valid_proposal(run.run_id)))}
    )
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.RESEARCHING
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1") is not None
        uow.commit()


@pytest.mark.integration
async def test_planning_invalid_proposal_degrades_idle_no_tasks(backend) -> None:
    run = _seed_planning(backend)
    empty = PlanProposal(
        run_id=run.run_id, plan_id="p1", task_specs=(), deliverable_paths=("report.md",)
    )
    coord = RunCoordinator(backend, {PhaseId.PLAN: _planner_handler(backend, _Planner(empty))})
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "plan-invalid" in report.reason
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1") is None  # task 5.10: no Tasks materialized
        uow.commit()


@pytest.mark.integration
async def test_planning_provider_failure_degrades_idle(backend) -> None:
    run = _seed_planning(backend)
    coord = RunCoordinator(backend, {PhaseId.PLAN: _planner_handler(backend, _FailingPlanner())})
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "failed" in report.reason


@pytest.mark.integration
async def test_planning_approval_required_opens_plan_approval_gate(backend) -> None:
    run = _seed_planning(backend)
    handler = PlanningPhaseHandler(
        _Planner(_valid_proposal(run.run_id)),
        limits=SystemLimits(),
        allowed_capabilities=ALL_CAPS,
        clock=backend.clock,
        idgen=backend.idgen,
        approval_required=True,
    )
    coord = RunCoordinator(backend, {PhaseId.PLAN: handler})
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.BLOCKED
    with backend.unit_of_work() as uow:
        gates = list(uow.gates.open_for_run(run.run_id))
        assert any(g.gate_type.value == "plan_approval" for g in gates)
        assert uow.tasks.get("t1") is None  # not materialized until the gate is approved
        uow.commit()

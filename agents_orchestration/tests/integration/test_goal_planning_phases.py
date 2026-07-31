"""Integration tests for Goal and Planning phase handlers (Ch.5 tasks 5.1-5.10)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import AdvanceDisposition, PhaseId
from agents_orchestration.domain.enums import CapabilityKind, RunState, WorkerRole
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.plan import (
    ExplorationBoundary,
    PlanAcceptance,
    ResearchExecutionMode,
    SeedExplorationBoundary,
    TaskSpec,
)
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.gates import GateContinuationError
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


def _agent_loop_proposal(run_id: str) -> PlanProposal:
    return PlanProposal(
        run_id=run_id,
        plan_id="loop-plan",
        research_execution_mode=ResearchExecutionMode.AGENT_LOOP,
        exploration_boundary=ExplorationBoundary(
            allowed_capabilities=(CapabilityKind.RAG_SEARCH,),
            seeds=(
                SeedExplorationBoundary(
                    task_id="seed-1",
                    required_coverage=(CapabilityKind.RAG_SEARCH,),
                    max_steps=5,
                    max_directions=2,
                    max_tokens=1_000,
                ),
            ),
        ),
        task_specs=(
            TaskSpec(
                task_id="seed-1",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="adaptive seed",
                required_capabilities=(CapabilityKind.RAG_SEARCH,),
            ),
        ),
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


@pytest.mark.integration
async def test_plan_approval_accepts_pending_plan_and_materializes_tasks(backend) -> None:
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
    service = OrchestrationService(backend, coordinator=coord)

    blocked = await service.advance_run(run.run_id)
    assert blocked.disposition is AdvanceDisposition.BLOCKED
    with backend.unit_of_work() as uow:
        pending = uow.plans.current(run.run_id)
        assert pending is not None
        assert pending.acceptance is PlanAcceptance.PROPOSED
        assert uow.tasks.get("t1") is None
        gate = next(iter(uow.gates.open_for_run(run.run_id)))
        uow.rollback()

    service.respond_gate(
        gate.gate_id,
        request_id="rq-plan-approved",
        actor="approver",
        role="orchestrator",
        payload={"outcome": "approved"},
    )

    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        accepted = uow.plans.current(run.run_id)
        assert final.state is RunState.RESEARCHING
        assert final.current_plan_version == 1
        assert accepted is not None
        assert accepted.acceptance is PlanAcceptance.ACCEPTED
        assert uow.tasks.get("t1") is not None
        uow.rollback()


@pytest.mark.integration
async def test_agent_loop_plan_approval_binds_boundary_hash_and_restores_mode(
    backend,
) -> None:
    run = _seed_planning(backend)
    handler = PlanningPhaseHandler(
        _Planner(_agent_loop_proposal(run.run_id)),
        limits=SystemLimits(),
        allowed_capabilities=ALL_CAPS,
        clock=backend.clock,
        idgen=backend.idgen,
        approval_required=True,
    )
    service = OrchestrationService(
        backend, coordinator=RunCoordinator(backend, {PhaseId.PLAN: handler})
    )

    await service.advance_run(run.run_id)
    with backend.unit_of_work() as uow:
        gate = next(iter(uow.gates.open_for_run(run.run_id)))
        pending = uow.plans.current(run.run_id)
        assert gate.artifact_hash == pending.graph.approval_hash()
        assert gate.continuation.bound_artifact_hash == gate.artifact_hash
        assert gate.context["research_execution_mode"] == "agent_loop"
        assert gate.context["seeds"][0]["task_id"] == "seed-1"
        assert gate.context["seeds"][0]["max_steps"] == 5
        assert gate.context["fixed_downstream_lifecycle"] == [
            "analyze",
            "write",
            "review",
            "finalize",
        ]
        gate_id = gate.gate_id

    service.respond_gate(
        gate_id,
        request_id="approve-loop",
        actor="approver",
        role="orchestrator",
        payload={"outcome": "approved"},
    )

    with backend.unit_of_work() as uow:
        accepted = uow.plans.current(run.run_id)
        task = uow.tasks.get("seed-1")
    assert accepted.graph.research_execution_mode is ResearchExecutionMode.AGENT_LOOP
    assert accepted.graph.exploration_boundary is not None
    assert task is not None


@pytest.mark.integration
async def test_plan_approval_invalidates_when_candidate_boundary_changes(backend) -> None:
    run = _seed_planning(backend)
    proposal = _agent_loop_proposal(run.run_id)
    handler = PlanningPhaseHandler(
        _Planner(proposal),
        limits=SystemLimits(),
        allowed_capabilities=ALL_CAPS,
        clock=backend.clock,
        idgen=backend.idgen,
        approval_required=True,
    )
    service = OrchestrationService(
        backend, coordinator=RunCoordinator(backend, {PhaseId.PLAN: handler})
    )
    await service.advance_run(run.run_id)
    with backend.unit_of_work() as uow:
        gate = next(iter(uow.gates.open_for_run(run.run_id)))
        changed = proposal.model_copy(update={"plan_id": "changed-boundary-plan"}).to_graph(2)
        from agents_orchestration.domain.plan import Plan

        uow.plans.save(Plan(run_id=run.run_id, graph=changed, proposed_at=NOW))
        uow.commit()

    with pytest.raises(GateContinuationError, match="candidate artifact drifted"):
        service.respond_gate(
            gate.gate_id,
            request_id="stale-approval",
            actor="approver",
            role="orchestrator",
            payload={"outcome": "approved"},
        )

    with backend.unit_of_work() as uow:
        canceled = uow.gates.get(gate.gate_id)
        assert canceled.state.value == "canceled"
        assert uow.tasks.get("seed-1") is None


# --- task 4.1 / 4.3: effective goal context flows into normalization -------


@pytest.mark.integration
async def test_goal_phase_normalizes_effective_goal_and_keeps_raw(backend) -> None:
    received: list[str] = []

    class _Recording:
        async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
            received.append(raw_goal)
            return GoalNormalizationOutcome(_goal(), _contract(), None)

    run = _seed(backend, RunState.NORMALIZING)
    with backend.unit_of_work() as uow:  # simulate a clarified consumption
        clarified = uow.runs.get(run.run_id).model_copy(
            update={"goal_clarification": "focus on pricing"}
        )
        uow.runs.save(clarified, expected_version=run.state_version)
        uow.commit()
    coord = RunCoordinator(backend, {PhaseId.GOAL: GoalPhaseHandler(_Recording(), backend.idgen)})
    await coord.advance(run.run_id)
    assert received == ["g\n\nUser clarification:\nfocus on pricing"]
    with backend.unit_of_work() as uow:  # task 4.3: raw_goal is never overwritten
        assert uow.runs.get(run.run_id).raw_goal == "g"
        uow.commit()


# --- task 4.4: clarification round-trip reaches PLANNING, no BLOCKED loop ----


@pytest.mark.integration
async def test_goal_clarification_round_trip_reaches_planning(backend) -> None:
    class _ClarifyingNormalizer:
        async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
            if "User clarification:" in raw_goal:
                return GoalNormalizationOutcome(_goal(), _contract(), None)  # clarified -> clear
            return GoalNormalizationOutcome(  # raw goal alone -> ambiguous
                GoalSpec(raw_input=raw_goal, objective="", deliverables=("report.md",)),
                _contract(),
                GoalClarificationProposal(
                    run_id=run_id, ambiguities=("objective",), questions=("objective?",)
                ),
            )

    coord = RunCoordinator(
        backend, {PhaseId.GOAL: GoalPhaseHandler(_ClarifyingNormalizer(), backend.idgen)}
    )
    service = OrchestrationService(backend, coordinator=coord)
    run = _seed(backend, RunState.NORMALIZING)

    blocked = await service.advance_run(run.run_id)  # ambiguous -> opens GOAL_CLARIFICATION
    assert blocked.disposition is AdvanceDisposition.BLOCKED

    with backend.unit_of_work() as uow:
        gate = next(iter(uow.gates.open_for_run(run.run_id)))
        gate_id = gate.gate_id
        uow.rollback()
    service.respond_gate(  # consume clarified -> same-state bump + goal_clarification
        gate_id,
        request_id="rq1",
        actor="approver",
        role="orchestrator",  # matches the role the coordinator opened the Gate with
        payload={"outcome": "clarified", "clarification": "focus on pricing"},
    )

    progressed = await service.advance_run(run.run_id)  # effective goal clear -> PLANNING
    assert progressed.disposition is AdvanceDisposition.PROGRESSED
    assert progressed.to_state is RunState.PLANNING  # no longer loops BLOCKED

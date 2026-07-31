"""Replan preservation tests (Ch.6 task 6.11)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.enums import CapabilityKind, RunState, TaskState, WorkerRole
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
)
from agents_orchestration.domain.plan import (
    ExplorationBoundary,
    ResearchExecutionMode,
    SeedExplorationBoundary,
    TaskSpec,
)
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import PlanProposal, ReplanProposal
from agents_orchestration.orchestration.replan import ReplanService

NOW = datetime(2026, 7, 28, tzinfo=UTC)
ALL_CAPS = frozenset(CapabilityKind)


def _seed_researching(backend):
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.PLANNING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )
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
    proposal = PlanProposal(
        run_id=run.run_id,
        plan_id="p1",
        task_specs=(
            TaskSpec(task_id="t1", worker_role=WorkerRole.EVIDENCE_RESEARCHER, description="r"),
            TaskSpec(task_id="t2", worker_role=WorkerRole.EVIDENCE_RESEARCHER, description="r2"),
        ),
        deliverable_paths=("report.md",),
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.completion.save(run.run_id, contract)
        validation = PlanValidator(SystemLimits()).validate(
            proposal, policy=run.policy, allowed_capabilities=ALL_CAPS, completion=contract
        )
        run = uow.runs.get(run.run_id)
        _plan, run = PlanAcceptor(uow, backend.clock, backend.idgen).accept(
            run, proposal, validation
        )
        uow.commit()
    # Mark t1 as an accepted (succeeded) research result.
    with backend.unit_of_work() as uow:
        t1 = uow.tasks.get("t1")
        uow.tasks.save(
            t1.transition(TaskState.SUCCEEDED, backend.clock.now(), accepted_attempt_id="att-1")
        )
        uow.commit()
    return run


def _seed_agent_loop_researching(backend):
    run = Run(
        run_id="agent-replan",
        raw_goal="g",
        state=RunState.PLANNING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )
    contract = CompletionContract(
        criteria=(),
        deliverable_paths=("report.md",),
    )
    specs = (
        TaskSpec(
            task_id="a1",
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            description="a1",
            required_capabilities=(CapabilityKind.RAG_SEARCH,),
        ),
        TaskSpec(
            task_id="a2",
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            description="a2",
            required_capabilities=(CapabilityKind.RAG_SEARCH,),
        ),
    )
    proposal = PlanProposal(
        run_id=run.run_id,
        plan_id="adaptive",
        research_execution_mode=ResearchExecutionMode.AGENT_LOOP,
        exploration_boundary=ExplorationBoundary(
            allowed_capabilities=(CapabilityKind.RAG_SEARCH,),
            seeds=tuple(
                SeedExplorationBoundary(
                    task_id=spec.task_id,
                    required_coverage=spec.required_capabilities,
                    max_steps=4,
                    max_directions=2,
                    max_tokens=40,
                )
                for spec in specs
            ),
        ),
        task_specs=specs,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.completion.save(run.run_id, contract)
        validation = PlanValidator(SystemLimits()).validate(
            proposal,
            policy=run.policy,
            allowed_capabilities=frozenset({CapabilityKind.RAG_SEARCH}),
            completion=contract,
        )
        _plan, run = PlanAcceptor(uow, backend.clock, backend.idgen).accept(
            run, proposal, validation
        )
        uow.commit()
    return run


@pytest.mark.integration
def test_replan_preserves_accepted_and_supersedes_invalidated(backend) -> None:
    run = _seed_researching(backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="evidence_gap",
        invalidate_task_ids=("t2",),
        add_task_specs=(
            TaskSpec(task_id="t3", worker_role=WorkerRole.EVIDENCE_RESEARCHER, description="gap"),
        ),
    )
    with backend.unit_of_work() as uow:
        run = uow.runs.get(run.run_id)
        plan, new_run = ReplanService(
            uow,
            PlanValidator(SystemLimits()),
            PlanAcceptor(uow, backend.clock, backend.idgen),
            backend.clock,
            backend.idgen,
        ).replan(run, proposal)
        uow.commit()

    assert plan.graph.version == 2  # new plan version
    assert new_run.replan_count == 1  # monotonic
    with backend.unit_of_work() as uow:
        t1 = uow.tasks.get("t1")  # preserved: still SUCCEEDED, promoted to v2
        t2 = uow.tasks.get("t2")  # invalidated -> SUPERSEDED
        t3 = uow.tasks.get("t3")  # focused addition -> PENDING on v2
        uow.commit()
    assert t1.state is TaskState.SUCCEEDED and t1.plan_version == 2
    assert t2.state is TaskState.SUPERSEDED
    assert t3.state is TaskState.PENDING and t3.plan_version == 2


@pytest.mark.integration
def test_replan_rejects_non_research_add(backend) -> None:
    """A Replan cannot add a non-research Task (remove-noop-phase-tasks 1.4)."""

    run = _seed_researching(backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="evidence_gap",
        add_task_specs=(
            TaskSpec(task_id="t3", worker_role=WorkerRole.REPORT_WRITER, description="write"),
        ),
    )
    with backend.unit_of_work() as uow:
        run = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match="non-research"):
            ReplanService(
                uow,
                PlanValidator(SystemLimits()),
                PlanAcceptor(uow, backend.clock, backend.idgen),
                backend.clock,
                backend.idgen,
            ).replan(run, proposal)
        uow.commit()


@pytest.mark.integration
def test_focused_replan_preserves_agent_mode_and_extends_seed_boundary(backend) -> None:
    run = _seed_agent_loop_researching(backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="research_gap",
        add_task_specs=(
            TaskSpec(
                task_id="a3",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap",
                required_capabilities=(CapabilityKind.RAG_SEARCH,),
            ),
        ),
    )
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        plan, _new_run = ReplanService(
            uow,
            PlanValidator(SystemLimits()),
            PlanAcceptor(uow, backend.clock, backend.idgen),
            backend.clock,
            backend.idgen,
        ).replan(current, proposal)
        uow.commit()

    assert plan.graph.research_execution_mode is ResearchExecutionMode.AGENT_LOOP
    assert plan.graph.exploration_boundary is not None
    assert {seed.task_id for seed in plan.graph.exploration_boundary.seeds} == {
        "a1",
        "a2",
        "a3",
    }


@pytest.mark.integration
def test_focused_replan_revalidates_worst_case_against_remaining_run_budget(
    backend,
) -> None:
    run = _seed_agent_loop_researching(backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="research_gap",
        add_task_specs=(
            TaskSpec(
                task_id="a3",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap",
                required_capabilities=(CapabilityKind.RAG_SEARCH,),
            ),
        ),
    )
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        budgeted = current.model_copy(update={"budget": Budget(max_tokens=100)})
        uow.runs.save(budgeted, expected_version=current.state_version)
        uow.commit()

    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match="seed token ceiling total"):
            ReplanService(
                uow,
                PlanValidator(SystemLimits()),
                PlanAcceptor(uow, backend.clock, backend.idgen),
                backend.clock,
                backend.idgen,
            ).replan(current, proposal)
        uow.commit()

    with backend.unit_of_work() as uow:
        persisted = uow.runs.get(run.run_id)
        plan = uow.plans.current(run.run_id)
        added = uow.tasks.get("a3")
    assert persisted.current_plan_version == 1
    assert plan.version == 1
    assert added is None


@pytest.mark.integration
def test_rejected_replan_does_not_mutate_current_plan_tasks(backend) -> None:
    """Role validation must run before any preserved Task is promoted.

    The caller is allowed to catch the validation error and still commit other
    UnitOfWork activity; a rejected proposal must therefore leave the current
    Run, Plan, and Task versions unchanged.
    """

    run = _seed_researching(backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="invalid_writer_task",
        add_task_specs=(
            TaskSpec(task_id="t3", worker_role=WorkerRole.REPORT_WRITER, description="write"),
        ),
    )

    with backend.unit_of_work() as uow:
        current_run = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match="non-research"):
            ReplanService(
                uow,
                PlanValidator(SystemLimits()),
                PlanAcceptor(uow, backend.clock, backend.idgen),
                backend.clock,
                backend.idgen,
            ).replan(current_run, proposal)
        uow.commit()

    with backend.unit_of_work() as uow:
        persisted_run = uow.runs.get(run.run_id)
        current_plan = uow.plans.current(run.run_id)
        t1 = uow.tasks.get("t1")
        t2 = uow.tasks.get("t2")
        t3 = uow.tasks.get("t3")
        uow.commit()

    assert persisted_run.current_plan_version == 1
    assert current_plan.version == 1
    assert t1.plan_version == 1
    assert t2.plan_version == 1
    assert t3 is None

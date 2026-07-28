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
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
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

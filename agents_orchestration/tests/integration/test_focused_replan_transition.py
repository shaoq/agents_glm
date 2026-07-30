"""Atomic focused replan + transition tests (tasks 4.4 / 4.5)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import CapabilityKind, RunState, TaskState, WorkerRole
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import ReplanProposal
from agents_orchestration.orchestration.replan import ReplanBudgetExhausted, ReplanService
from agents_orchestration.runtime.ports import StaleVersionError
from tests.integration.test_replan_preserves import _seed_researching

_CORRELATION = {"gap_id": "gap:abc", "focus_hash": "focus:def", "source_phase": "analyze"}


def _to_analyzing(run, backend):
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        moved = current.transition(RunState.ANALYZING, backend.clock.now())
        uow.runs.save(moved, expected_version=current.state_version)
        uow.commit()
    return moved


def _service(uow, backend) -> ReplanService:
    return ReplanService(
        uow,
        PlanValidator(SystemLimits()),
        PlanAcceptor(uow, backend.clock, backend.idgen),
        backend.clock,
        backend.idgen,
    )


def _gap_proposal(run_id: str, *, task_id: str = "t3") -> ReplanProposal:
    return ReplanProposal(
        run_id=run_id,
        reason="research_gap",
        add_task_specs=(
            TaskSpec(
                task_id=task_id,
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap",
            ),
        ),
    )


# --- 4.4 atomic success ----------------------------------------------------


@pytest.mark.integration
def test_replan_and_transition_commits_plan_and_run_in_one_cas(backend) -> None:
    run = _to_analyzing(_seed_researching(backend), backend)

    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        before_sv = current.state_version
        plan, new_run = _service(uow, backend).replan_and_transition(
            current,
            _gap_proposal(current.run_id),
            transition_to=RunState.RESEARCHING,
            correlation=_CORRELATION,
        )
        uow.commit()

    assert plan.graph.version == 2
    assert new_run.state is RunState.RESEARCHING
    assert new_run.current_plan_version == 2
    assert new_run.replan_count == 1
    # Exactly one Run CAS: state_version advanced by exactly one.
    assert new_run.state_version == before_sv + 1

    with backend.unit_of_work() as uow:
        t1 = uow.tasks.get("t1")  # preserved SUCCEEDED, promoted to v2
        t3 = uow.tasks.get("t3")  # new PENDING research task on v2
        effects = [e.effect.value for e in uow.events.stream(run.run_id)]
        replan_events = [
            e for e in uow.events.stream(run.run_id) if e.effect.value == "plan_replanned"
        ]
        uow.commit()
    assert t1.state is TaskState.SUCCEEDED and t1.plan_version == 2
    assert t3.state is TaskState.PENDING and t3.plan_version == 2
    assert t3.worker_role is WorkerRole.EVIDENCE_RESEARCHER
    assert "run_state_transition" in effects
    assert len(replan_events) == 1
    payload = replan_events[0].payload
    assert payload["gap_id"] == "gap:abc"
    assert payload["old_plan_version"] == 1 and payload["new_plan_version"] == 2
    assert payload["added"] == ["t3"] and "t1" in payload["preserved"]


@pytest.mark.integration
def test_replan_and_transition_preserves_accepted_evidence(backend) -> None:
    run = _to_analyzing(_seed_researching(backend), backend)
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        _service(uow, backend).replan_and_transition(
            current, _gap_proposal(current.run_id), transition_to=RunState.RESEARCHING
        )
        uow.commit()
    with backend.unit_of_work() as uow:
        t1 = uow.tasks.get("t1")
        uow.commit()
    assert t1.accepted_attempt_id == "att-1"  # accepted evidence preserved


# --- 4.4 invalid proposal -> zero write -----------------------------------


@pytest.mark.integration
def test_non_research_add_rejected_with_no_partial_write(backend) -> None:
    run = _to_analyzing(_seed_researching(backend), backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="x",
        add_task_specs=(
            TaskSpec(task_id="t3", worker_role=WorkerRole.REPORT_WRITER, description="write"),
        ),
    )
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match="non-research"):
            _service(uow, backend).replan_and_transition(
                current, proposal, transition_to=RunState.RESEARCHING
            )
        uow.commit()
    with backend.unit_of_work() as uow:
        persisted = uow.runs.get(run.run_id)
        plan = uow.plans.current(run.run_id)
        uow.commit()
    assert persisted.current_plan_version == 1
    assert persisted.state is RunState.ANALYZING  # unchanged
    assert plan.version == 1


@pytest.mark.integration
def test_empty_add_rejected_with_no_write(backend) -> None:
    run = _to_analyzing(_seed_researching(backend), backend)
    proposal = ReplanProposal(run_id=run.run_id, reason="x")
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match="at least one"):
            _service(uow, backend).replan_and_transition(
                current, proposal, transition_to=RunState.RESEARCHING
            )
        uow.commit()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("task_spec", "diagnostic"),
    [
        (
            TaskSpec(
                task_id="t3",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap",
                depends_on=("missing-task",),
            ),
            "unknown task",
        ),
        (
            TaskSpec(
                task_id="t3",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap",
                required_capabilities=(CapabilityKind.WEB_RESEARCH,),
            ),
            "unsupported capability",
        ),
    ],
)
def test_plan_validator_rejects_invalid_candidate_before_any_write(
    backend, task_spec, diagnostic
) -> None:
    """A caller may catch validation and still commit; invalid candidates must
    therefore be rejected before preserved Tasks are promoted to Plan v2."""

    run = _to_analyzing(_seed_researching(backend), backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="research_gap",
        add_task_specs=(task_spec,),
    )

    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match=diagnostic):
            _service(uow, backend).replan_and_transition(
                current, proposal, transition_to=RunState.RESEARCHING
            )
        uow.commit()

    with backend.unit_of_work() as uow:
        persisted_run = uow.runs.get(run.run_id)
        persisted_plan = uow.plans.current(run.run_id)
        t1 = uow.tasks.get("t1")
        t3 = uow.tasks.get("t3")
        uow.commit()

    assert persisted_run.current_plan_version == 1
    assert persisted_run.state is RunState.ANALYZING
    assert persisted_plan.version == 1
    assert t1.plan_version == 1
    assert t3 is None


@pytest.mark.integration
def test_plan_validator_rejects_depends_on_cycle_before_any_write(backend) -> None:
    run = _to_analyzing(_seed_researching(backend), backend)
    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="research_gap",
        add_task_specs=(
            TaskSpec(
                task_id="t3",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap A",
                depends_on=("t4",),
            ),
            TaskSpec(
                task_id="t4",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gap B",
                depends_on=("t3",),
            ),
        ),
    )

    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        with pytest.raises(ValueError, match="cycle"):
            _service(uow, backend).replan_and_transition(
                current, proposal, transition_to=RunState.RESEARCHING
            )
        uow.commit()

    with backend.unit_of_work() as uow:
        plan = uow.plans.current(run.run_id)
        t1 = uow.tasks.get("t1")
        t3 = uow.tasks.get("t3")
        t4 = uow.tasks.get("t4")
        uow.commit()
    assert plan.version == 1
    assert t1.plan_version == 1
    assert t3 is None and t4 is None


@pytest.mark.integration
def test_budget_exhausted_raises_with_no_write(backend) -> None:

    run = _to_analyzing(_seed_researching(backend), backend)
    # Burn the entire replan budget up front.
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        exhausted = current.model_copy(update={"replan_count": current.policy.max_replans})
        uow.runs.save(exhausted, expected_version=current.state_version)
        uow.commit()
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        with pytest.raises(ReplanBudgetExhausted):
            _service(uow, backend).replan_and_transition(
                current, _gap_proposal(current.run_id), transition_to=RunState.RESEARCHING
            )
        uow.commit()
    with backend.unit_of_work() as uow:
        plan = uow.plans.current(run.run_id)
        uow.commit()
    assert plan.version == 1  # no new plan created


# --- 4.4 CAS/repository failure -> full rollback --------------------------


@pytest.mark.integration
def test_stale_run_version_rolls_back_all_writes(backend) -> None:
    """4.5: when the Run version drifted during the provider call, the candidate
    replan raises StaleVersionError and leaves no partial Plan/Task."""

    run = _to_analyzing(_seed_researching(backend), backend)
    # Simulate a concurrent advance: the DB state_version moves ahead of the
    # snapshot the caller holds.
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        bumped = current.bump_version(backend.clock.now())
        uow.runs.save(bumped, expected_version=current.state_version)
        uow.commit()

    stale_snapshot = run.model_copy(update={"state_version": run.state_version})  # old version
    with backend.unit_of_work() as uow:
        with pytest.raises(StaleVersionError):
            _service(uow, backend).replan_and_transition(
                stale_snapshot, _gap_proposal(run.run_id), transition_to=RunState.RESEARCHING
            )
        # Intentionally NOT committed: a failed replan must roll back, so the
        # UnitOfWork rollback on exit discards the buffered Plan v+1 / new Task.
    with backend.unit_of_work() as uow:
        plan = uow.plans.current(run.run_id)
        t3 = uow.tasks.get("t3")
        uow.commit()
    assert plan.version == 1  # no plan v2 committed
    assert t3 is None  # no new task committed

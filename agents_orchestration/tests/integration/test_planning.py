"""Integration tests for goal normalization and dynamic planning (Section 5)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import CapabilityKind, RunState, TaskState, WorkerRole
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.plan import Dependency, TaskSpec
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.goal import GoalService
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import PlanProposal, ReplanProposal
from agents_orchestration.orchestration.replan import ReplanService

ALLOWED = frozenset({CapabilityKind.MEMORY_RECALL, CapabilityKind.RAG_SEARCH})


def _completion() -> CompletionContract:
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


def _research_spec(task_id: str, deps: tuple[str, ...] = ()) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        description=task_id,
        required_capabilities=(CapabilityKind.RAG_SEARCH,),
        depends_on=deps,
        depth=1,
    )


def _proposal(task_ids=("t1",), deps=()) -> PlanProposal:
    specs = tuple(_research_spec(tid, deps=tuple()) for tid in task_ids)
    return PlanProposal(
        run_id="r1",
        plan_id="p1",
        task_specs=specs,
        dependencies=tuple(deps),
        deliverable_paths=("report.md",),
    )


def _run_planning(backend, clock) -> None:
    now = clock.now()
    run = Run(
        run_id="r1",
        raw_goal="g",
        state=RunState.PLANNING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=now,
        updated_at=now,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.completion.save("r1", _completion())
        uow.commit()


# --- 5.5 PlanValidator ------------------------------------------------------


@pytest.mark.integration
def test_validator_accepts_valid_plan(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        result = PlanValidator(SystemLimits()).validate(
            _proposal(("t1", "t2")),
            policy=run.policy,
            allowed_capabilities=ALLOWED,
            completion=_completion(),
        )
    assert result.accepted and result.graph is not None


@pytest.mark.integration
def test_validator_rejects_cycle_and_unsupported_capability(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    cyclic = PlanProposal(
        run_id="r1",
        plan_id="p1",
        task_specs=(_research_spec("a"), _research_spec("b")),
        dependencies=(
            Dependency(predecessor="a", successor="b"),
            Dependency(predecessor="b", successor="a"),
        ),
        deliverable_paths=("report.md",),
    )
    unsupported = TaskSpec(
        task_id="x",
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        description="x",
        required_capabilities=(CapabilityKind.WEB_RESEARCH,),
    )
    bad = PlanProposal(
        run_id="r1",
        plan_id="p1",
        task_specs=(unsupported,),
        deliverable_paths=("report.md",),
    )
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        v = PlanValidator(SystemLimits())
        cyc = v.validate(
            cyclic, policy=run.policy, allowed_capabilities=ALLOWED, completion=_completion()
        )
        unsup = v.validate(
            bad, policy=run.policy, allowed_capabilities=ALLOWED, completion=_completion()
        )
    assert not cyc.accepted and any("cycle" in d for d in cyc.diagnostics)
    assert not unsup.accepted and any("unsupported capability" in d for d in unsup.diagnostics)


@pytest.mark.integration
def test_validator_rejects_missing_deliverable(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    proposal = PlanProposal(
        run_id="r1", plan_id="p1", task_specs=(_research_spec("t1"),), deliverable_paths=()
    )
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        result = PlanValidator(SystemLimits()).validate(
            proposal,
            policy=run.policy,
            allowed_capabilities=ALLOWED,
            completion=_completion(),
        )
    assert not result.accepted
    assert any("report.md" in d for d in result.diagnostics)


# --- 5.6 / 5.7 PlanAcceptor -------------------------------------------------


@pytest.mark.integration
def test_acceptor_materializes_tasks_and_advances_run(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        validator = PlanValidator(SystemLimits())
        validation = validator.validate(
            _proposal(("t1", "t2")),
            policy=run.policy,
            allowed_capabilities=ALLOWED,
            completion=_completion(),
        )
        plan, new_run = PlanAcceptor(uow, fake_clock, backend.idgen).accept(
            run, _proposal(("t1", "t2")), validation
        )
        uow.commit()
    assert plan.acceptance.value == "accepted"
    assert new_run.current_plan_version == 1
    assert new_run.state is RunState.RESEARCHING
    with backend.unit_of_work() as uow:
        assert {t.task_id for t in uow.tasks.by_run("r1", 1)} == {"t1", "t2"}


@pytest.mark.integration
def test_acceptor_rejects_without_materializing_tasks(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    bad = PlanProposal(run_id="r1", plan_id="p1", task_specs=(), deliverable_paths=("report.md",))
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        validation = PlanValidator(SystemLimits()).validate(
            bad,
            policy=run.policy,
            allowed_capabilities=ALLOWED,
            completion=_completion(),
        )
        PlanAcceptor(uow, fake_clock, backend.idgen).accept(run, bad, validation)
        uow.commit()
    assert not validation.accepted
    with backend.unit_of_work() as uow:
        assert uow.tasks.by_run("r1") == []


# --- 5.3 / 5.8 Goal ambiguity + completion amendment ------------------------


@pytest.mark.integration
def test_goal_service_detects_ambiguity_and_amends_completion(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    ambiguous = GoalSpec(raw_input="", objective="", deliverables=())
    clear = GoalSpec(raw_input="x", objective="summarize X", deliverables=("report.md",))
    with backend.unit_of_work() as uow:
        svc = GoalService(uow, fake_clock, backend.idgen)
        assert svc.detect_ambiguity(ambiguous, "r1") is not None
        assert svc.detect_ambiguity(clear, "r1") is None

        run = uow.runs.get("r1")
        contract = _completion()
        amended, new_run = svc.amend_completion(
            run,
            contract,
            actor="reviewer",
            reason="tighten",
            new_criteria=contract.criteria
            + (
                CompletionCriterion(kind=CriterionKind.CITATION_INTEGRITY, description="citations"),
            ),
        )
        uow.commit()
    assert amended.version == 2
    assert new_run.state_version == 2
    with backend.unit_of_work() as uow:
        assert uow.completion.get("r1").version == 2


# --- 5.9 Replan -------------------------------------------------------------


@pytest.mark.integration
def test_replan_preserves_succeeded_supersedes_invalidated_adds_new(backend, fake_clock) -> None:
    _run_planning(backend, fake_clock)
    # Accept an initial 2-task plan and mark t1 SUCCEEDED.
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        validation = PlanValidator(SystemLimits()).validate(
            _proposal(("t1", "t2")),
            policy=run.policy,
            allowed_capabilities=ALLOWED,
            completion=_completion(),
        )
        PlanAcceptor(uow, fake_clock, backend.idgen).accept(
            run, _proposal(("t1", "t2")), validation
        )
        uow.commit()
    with backend.unit_of_work() as uow:
        uow.tasks.save(
            uow.tasks.get("t1").transition(
                TaskState.SUCCEEDED, fake_clock.now(), accepted_attempt_id="a1"
            )
        )
        uow.commit()

    proposal = ReplanProposal(
        run_id="r1",
        reason="evidence_gap",
        invalidate_task_ids=("t2",),
        add_task_specs=(_research_spec("t3"),),
    )
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        validator = PlanValidator(SystemLimits())
        acceptor = PlanAcceptor(uow, fake_clock, backend.idgen)
        ReplanService(uow, validator, acceptor, fake_clock, backend.idgen).replan(run, proposal)
        uow.commit()

    with backend.unit_of_work() as uow:
        assert uow.runs.get("r1").current_plan_version == 2
        assert uow.runs.get("r1").replan_count == 1
        t1 = uow.tasks.get("t1")
        t2 = uow.tasks.get("t2")
        t3 = uow.tasks.get("t3")
        assert t1.state is TaskState.SUCCEEDED and t1.plan_version == 2  # preserved w/ result
        assert t2.state is TaskState.SUPERSEDED
        assert t3.state is TaskState.PENDING and t3.plan_version == 2

"""Integration tests for accepted-evidence persistence (Ch.2 tasks 2.2-2.4)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.evidence import Evidence, SourceIdentity, SourceKind


def _ev(eid: str = "e1") -> Evidence:
    return Evidence(
        evidence_id=eid,
        source=SourceIdentity(source_id=f"s-{eid}", source_kind=SourceKind.RAG, uri=f"u-{eid}"),
        content_text="passage",
    )


@pytest.mark.integration
def test_evidence_save_and_read(backend) -> None:
    with backend.unit_of_work() as uow:
        uow.evidence.save_many("r1", "a1", [_ev("e1"), _ev("e2")])
        uow.commit()
    with backend.unit_of_work() as uow:
        got = uow.evidence.by_run("r1")
    assert [e.evidence_id for e in got] == ["e1", "e2"]
    assert got[0].source.source_kind is SourceKind.RAG
    assert got[0].content_text == "passage"


@pytest.mark.integration
def test_evidence_rollback_not_visible(backend) -> None:
    """Atomicity: uncommitted evidence MUST NOT be visible (task 2.4)."""

    with backend.unit_of_work() as uow:
        uow.evidence.save_many("r1", "a1", [_ev("e1")])
        uow.rollback()
    with backend.unit_of_work() as uow:
        assert uow.evidence.by_run("r1") == []


@pytest.mark.integration
def test_evidence_upsert_by_run_and_id(backend) -> None:
    """Same (run_id, evidence_id) upserts instead of duplicating."""

    with backend.unit_of_work() as uow:
        uow.evidence.save_many("r1", "a1", [_ev("e1")])
        uow.commit()
    with backend.unit_of_work() as uow:
        uow.evidence.save_many("r1", "a2", [_ev("e1")])
        uow.commit()
    with backend.unit_of_work() as uow:
        got = uow.evidence.by_run("r1")
    assert len(got) == 1


@pytest.mark.integration
async def test_tick_accept_persists_evidence(backend, fake_clock) -> None:
    """End-to-end: a Task whose TaskResult carries evidence persists it on accept,
    so evidence_provider can read it (tasks 2.2 / 2.3)."""

    from agents_orchestration.domain.enums import RunState, WorkerRole
    from agents_orchestration.domain.execution import Run, Task
    from agents_orchestration.domain.plan import Plan, PlanGraph
    from agents_orchestration.domain.policy import RunPolicy, SystemLimits
    from agents_orchestration.domain.worker import TaskResult
    from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome

    now = fake_clock.now()
    policy = RunPolicy.from_limits(SystemLimits())
    run = Run(
        run_id="r1",
        raw_goal="g",
        state=RunState.RESEARCHING,
        policy=policy,
        current_plan_version=1,
        created_at=now,
        updated_at=now,
    )
    task = Task(
        task_id="t1",
        run_id="r1",
        plan_version=1,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        required_capabilities=(CapabilityKind.RAG_SEARCH,),
        created_at=now,
        updated_at=now,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.plans.save(Plan(run_id="r1", graph=PlanGraph(plan_id="p1", version=1), proposed_at=now))
        uow.tasks.materialize([task])
        uow.commit()

    class _Executor:
        async def execute(self, task, attempt, run):
            return TaskExecutionOutcome(
                succeeded=True,
                task_result=TaskResult(
                    attempt_id=attempt.attempt_id,
                    task_id=task.task_id,
                    run_id=run.run_id,
                    worker_role=task.worker_role,
                    evidence=(_ev("r-e1"),),
                    summary="research",
                ),
            )

    tick = RuntimeTick(backend, executor=_Executor(), limits=SystemLimits())
    report = await tick.tick("r1")
    assert report.accepted == 1

    with backend.unit_of_work() as uow:
        got = uow.evidence.by_run("r1")
    assert len(got) == 1 and got[0].evidence_id == "r-e1"

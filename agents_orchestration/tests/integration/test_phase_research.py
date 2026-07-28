"""Integration tests for phase-aware Task runtime and Research join (Ch.6)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    PhaseId,
    eligible_worker_roles,
)
from agents_orchestration.domain.enums import (
    CapabilityKind,
    RunState,
    TaskState,
    WorkerRole,
)
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.phases import ResearchPhaseHandler
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome, TickReport

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _seed_run(backend, state: RunState = RunState.RESEARCHING, plan_version: int = 1) -> Run:
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g", state=state,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=plan_version,
        created_at=NOW, updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    return run


def _seed_task(backend, run: Run, task_id: str, role: WorkerRole, state: TaskState) -> Task:
    task = Task(
        task_id=task_id, run_id=run.run_id, plan_version=run.current_plan_version or 1,
        worker_role=role, state=state,
        required_capabilities=(CapabilityKind.RAG_SEARCH,),
        created_at=NOW, updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.tasks.materialize([task])
        uow.commit()
    return task


# --- Task 6.1 / 6.2: phase -> eligible Worker role -------------------------


@pytest.mark.unit
def test_eligible_worker_roles_maps_each_task_phase() -> None:
    expected = {
        RunState.RESEARCHING: frozenset({WorkerRole.EVIDENCE_RESEARCHER}),
        RunState.ANALYZING: frozenset({WorkerRole.ANALYST}),
        RunState.WRITING: frozenset({WorkerRole.REPORT_WRITER}),
        RunState.REVIEWING: frozenset({WorkerRole.REPORT_REVIEWER}),
    }
    for state, roles in expected.items():
        assert eligible_worker_roles(state) == roles


@pytest.mark.unit
def test_eligible_worker_roles_none_for_non_task_phases() -> None:
    for state in (RunState.NORMALIZING, RunState.PLANNING, RunState.FINALIZING, RunState.PAUSED):
        assert eligible_worker_roles(state) is None


# --- Task 6.4: tick only dispatches the current phase's roles --------------


class _FakeExecutor:
    async def execute(self, task: Task, attempt, run: Run) -> TaskExecutionOutcome:
        return TaskExecutionOutcome(succeeded=True)


@pytest.mark.integration
async def test_tick_filters_dispatch_by_phase_role(backend) -> None:
    run = _seed_run(backend, RunState.RESEARCHING)
    _seed_task(backend, run, "research-1", WorkerRole.EVIDENCE_RESEARCHER, TaskState.PENDING)
    _seed_task(backend, run, "writer-1", WorkerRole.REPORT_WRITER, TaskState.PENDING)
    tick = RuntimeTick(backend, executor=_FakeExecutor(), limits=SystemLimits())
    report = await tick.tick(run.run_id)
    assert report.dispatched == 1  # only the EVIDENCE_RESEARCHER Task
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("research-1").state is TaskState.SUCCEEDED
        assert uow.tasks.get("writer-1").state is TaskState.PENDING  # not dispatched
        uow.commit()


# --- Task 6.7 / 6.8 / 6.10: ResearchPhaseHandler ---------------------------


class _FakeTick:
    def __init__(self, *, dispatched: int = 0, accepted: int = 0) -> None:
        self._r = TickReport("run", dispatched=dispatched, accepted=accepted)

    async def tick(self, run_id: str) -> TickReport:
        return self._r


async def _evidence_empty(run_id: str):
    return ()


def _research_handler(backend, tick, provider=None) -> ResearchPhaseHandler:
    return ResearchPhaseHandler(
        tick, provider or _evidence_empty, clock=backend.clock, idgen=backend.idgen
    )


@pytest.mark.integration
async def test_research_in_flight_delegates_tick_and_progresses(backend) -> None:
    run = _seed_run(backend, RunState.RESEARCHING)
    _seed_task(backend, run, "r1", WorkerRole.EVIDENCE_RESEARCHER, TaskState.PENDING)
    coord = RunCoordinator(
        backend, {PhaseId.RESEARCH: _research_handler(backend, _FakeTick(dispatched=1))}
    )
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.task_tick is not None and report.task_tick.dispatched == 1
    # Stay in RESEARCHING — the Task is still in flight.
    assert report.to_state is RunState.RESEARCHING


@pytest.mark.integration
async def test_research_all_succeeded_joins_to_analyzing(backend) -> None:
    run = _seed_run(backend, RunState.RESEARCHING)
    _seed_task(backend, run, "r1", WorkerRole.EVIDENCE_RESEARCHER, TaskState.SUCCEEDED)
    coord = RunCoordinator(
        backend, {PhaseId.RESEARCH: _research_handler(backend, _FakeTick())}
    )
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.ANALYZING
    assert "joined" in report.reason


@pytest.mark.integration
async def test_research_failed_task_degrades_idle(backend) -> None:
    run = _seed_run(backend, RunState.RESEARCHING)
    _seed_task(backend, run, "r1", WorkerRole.EVIDENCE_RESEARCHER, TaskState.FAILED)
    coord = RunCoordinator(
        backend, {PhaseId.RESEARCH: _research_handler(backend, _FakeTick())}
    )
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "failed" in report.reason

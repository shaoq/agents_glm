"""Integration tests for tick Phase 2 concurrency (task 5.2).

Verifies the ``asyncio.gather + Semaphore(max_concurrency)`` change: dispatches
run concurrently (peak > 1) and the concurrency is bounded by
``run.policy.max_concurrency``. Crash-recovery semantics are unchanged because
Phase 2 still executes outside any transaction with per-Task Leases claimed in
Phase 1 and batch-accepted in Phase 3 — those are covered by test_recovery.py.
"""

from __future__ import annotations

import asyncio

import pytest

from agents_orchestration.domain.enums import RunState, WorkerRole
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.plan import Plan, PlanGraph
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome


class _ConcurrencyProbeExecutor:
    """Records peak concurrent executions; each sleeps briefly so siblings overlap."""

    def __init__(self) -> None:
        self.peak = 0
        self._current = 0
        self.calls: list[str] = []

    async def execute(self, task: Task, attempt: Attempt, run: Run) -> TaskExecutionOutcome:
        self.calls.append(task.task_id)
        self._current += 1
        self.peak = max(self.peak, self._current)
        await asyncio.sleep(0.02)  # yield so concurrent dispatches overlap
        self._current -= 1
        return TaskExecutionOutcome(succeeded=True)


def _seed_concurrent_run(backend, clock, *, n_tasks: int, max_concurrency: int) -> str:
    now = clock.now()
    policy = RunPolicy.from_limits(SystemLimits(), max_concurrency=max_concurrency)
    run = Run(
        run_id="r-conc",
        raw_goal="g",
        state=RunState.RESEARCHING,
        policy=policy,
        budget=Budget(),
        current_plan_version=1,
        created_at=now,
        updated_at=now,
    )
    tasks = [
        Task(
            task_id=f"t{i}",
            run_id="r-conc",
            plan_version=1,
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            depth=1,
            created_at=now,
            updated_at=now,
        )
        for i in range(n_tasks)
    ]
    plan = Plan(run_id="r-conc", graph=PlanGraph(plan_id="p1", version=1), proposed_at=now)
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.plans.save(plan)
        uow.tasks.materialize(tasks)
        uow.commit()
    return "r-conc"


@pytest.mark.integration
async def test_phase2_dispatches_run_concurrently(backend, fake_clock) -> None:
    run_id = _seed_concurrent_run(backend, fake_clock, n_tasks=2, max_concurrency=2)
    probe = _ConcurrencyProbeExecutor()
    tick = RuntimeTick(backend, executor=probe, limits=SystemLimits())

    report = await tick.tick(run_id)

    assert report.dispatched == 2
    assert report.accepted == 2
    # Both ran concurrently — a serial for-await would leave peak at 1.
    assert probe.peak == 2


@pytest.mark.integration
async def test_phase2_concurrency_bounded_by_policy(backend, fake_clock) -> None:
    # 4 ready tasks but max_concurrency=2 → one tick dispatches 2, peak never > 2
    run_id = _seed_concurrent_run(backend, fake_clock, n_tasks=4, max_concurrency=2)
    probe = _ConcurrencyProbeExecutor()
    tick = RuntimeTick(backend, executor=probe, limits=SystemLimits())

    report = await tick.tick(run_id)

    assert report.dispatched == 2  # scheduler caps a single tick at max_concurrency
    assert probe.peak <= 2
    assert probe.peak > 1  # still genuinely concurrent, not serial

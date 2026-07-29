"""Tests for task retry re-dispatch (AWAITING_RETRY → READY) and phase-level IDLE
bounded give-up (change: complete-runtime-retry-and-stage-replay)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    InputFingerprint,
    PhaseId,
)
from agents_orchestration.domain.enums import (
    FailureCode,
    RunState,
    TaskState,
    TerminationReason,
)
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.coordinator import PhaseOutcome, RunCoordinator
from agents_orchestration.runtime.core import retry_backoff_seconds
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome
from tests.integration.test_run_coordinator import _FakeHandler
from tests.integration.test_run_coordinator import _seed as _seed_coord
from tests.integration.test_runtime import _FakeExecutor, _retryable_fail, _seed

# === candidate 1: retry_backoff_seconds helper =============================


@pytest.mark.unit
def test_retry_backoff_seconds_formula_and_cap() -> None:
    assert retry_backoff_seconds(1, base=1.0) == 1.0
    assert retry_backoff_seconds(2, base=1.0) == 2.0
    assert retry_backoff_seconds(3, base=1.0) == 4.0
    assert retry_backoff_seconds(7, base=1.0) == 60.0  # 2**6 = 64 -> capped at 60
    assert retry_backoff_seconds(3, base=2.0) == 8.0


# === candidate 1: AWAITING_RETRY → READY re-dispatch =======================


@pytest.mark.integration
async def test_tick_readmits_retry_ready_after_backoff(backend, fake_clock) -> None:
    """A retryable failure enters AWAITING_RETRY; once backoff elapses the next
    tick re-queues it to READY and re-dispatches — no manual transition needed
    (the bug was that it never re-dispatched and the Run stalled)."""

    _seed(backend, fake_clock, task_ids=("t1",))
    calls = {"n": 0}

    def _fail_once_then_succeed(_task):
        calls["n"] += 1
        if calls["n"] == 1:
            return TaskExecutionOutcome(succeeded=False, failure_code=FailureCode.TIMEOUT)
        return TaskExecutionOutcome(succeeded=True)

    tick = RuntimeTick(
        backend, executor=_FakeExecutor(_fail_once_then_succeed), limits=SystemLimits()
    )

    await tick.tick("r1")  # attempt 1 -> TIMEOUT -> AWAITING_RETRY (backoff = 1s)
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.AWAITING_RETRY

    report = await tick.tick("r1")  # backoff due -> READY -> attempt 2 -> SUCCEEDED
    assert report.accepted == 1
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.SUCCEEDED


@pytest.mark.integration
async def test_tick_keeps_awaiting_retry_when_backoff_not_due(backend, fake_clock) -> None:
    """When backoff has not elapsed, AWAITING_RETRY stays put (not re-dispatched)."""

    _seed(backend, fake_clock, task_ids=("t1",))
    tick = RuntimeTick(backend, executor=_FakeExecutor(_retryable_fail), limits=SystemLimits())

    await tick.tick("r1")  # -> AWAITING_RETRY
    with backend.unit_of_work() as uow:
        task = uow.tasks.get("t1")
        assert task.state is TaskState.AWAITING_RETRY
        # Push updated_at into the future so backoff is nowhere near due.
        uow.tasks.save(
            task.model_copy(update={"updated_at": task.updated_at + timedelta(seconds=1000)})
        )
        uow.commit()

    report = await tick.tick("r1")  # backoff not due -> no READY -> blocked
    assert report.blocked
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.AWAITING_RETRY


# === candidate 2': phase IDLE bounded give-up ==============================


def _idle_goal_handler() -> _FakeHandler:
    return _FakeHandler(
        PhaseId.GOAL,
        PhaseOutcome(
            AdvanceDisposition.IDLE,
            input_fingerprint=InputFingerprint(state_version=1),
            stage_logical_key="goal",
            reason="normalizer-pending",
        ),
    )


@pytest.mark.integration
async def test_phase_idle_under_budget_keeps_idle(backend) -> None:
    """Below the observation budget, IDLE observations accumulate but the Run is
    not terminated — it stays IDLE for another advance."""

    coord = RunCoordinator(backend, {PhaseId.GOAL: _idle_goal_handler()})
    run = _seed_coord(backend, RunState.NORMALIZING)

    first = await coord.advance(run.run_id)
    second = await coord.advance(run.run_id)
    assert first.disposition is AdvanceDisposition.IDLE
    assert second.disposition is AdvanceDisposition.IDLE
    with backend.unit_of_work() as uow:
        assert uow.runs.get(run.run_id).state is RunState.NORMALIZING


@pytest.mark.integration
async def test_phase_idle_bounded_give_up_after_observation_budget(backend) -> None:
    """Once PREPARED observations on the same logical_stage reach
    max_attempts_per_task, the Run is bounded-terminated (ATTEMPTS_EXHAUSTED)."""

    coord = RunCoordinator(backend, {PhaseId.GOAL: _idle_goal_handler()})
    run = _seed_coord(backend, RunState.NORMALIZING)

    await coord.advance(run.run_id)  # PREPARED = 1 -> IDLE
    await coord.advance(run.run_id)  # PREPARED = 2 -> IDLE
    third = await coord.advance(run.run_id)  # PREPARED = 3 >= budget -> TERMINAL

    assert third.disposition is AdvanceDisposition.TERMINAL
    assert third.reason == TerminationReason.ATTEMPTS_EXHAUSTED.value
    assert third.to_state is RunState.FAILED


@pytest.mark.integration
async def test_repeated_idle_advance_terminates_within_budget(backend) -> None:
    """A permanently-IDLE phase terminates within the observation budget, not
    after spinning drive_run to max_advances."""

    coord = RunCoordinator(backend, {PhaseId.GOAL: _idle_goal_handler()})
    run = _seed_coord(backend, RunState.NORMALIZING)

    last = None
    advances = 0
    for _ in range(50):  # far below drive_run's max_advances (1000)
        last = await coord.advance(run.run_id)
        advances += 1
        if last.disposition is AdvanceDisposition.TERMINAL:
            break

    assert last.disposition is AdvanceDisposition.TERMINAL
    assert advances <= run.policy.max_attempts_per_task

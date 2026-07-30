"""Tests for task retry re-dispatch (AWAITING_RETRY → READY) and phase-level IDLE
bounded give-up (change: complete-runtime-retry-and-stage-replay)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    InputFingerprint,
    PhaseId,
)
from agents_orchestration.domain.enums import (
    CapabilityKind,
    FailureCode,
    RunState,
    TaskState,
    TerminationReason,
)
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.composition import build_production_coordinator
from agents_orchestration.orchestration.coordinator import (
    PhaseContext,
    PhaseOutcome,
    RunCoordinator,
)
from agents_orchestration.runtime.core import retry_backoff_seconds
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome
from tests.integration.test_run_coordinator import _FakeHandler
from tests.integration.test_run_coordinator import _seed as _seed_coord
from tests.integration.test_runtime import _FakeExecutor, _retryable_fail, _seed
from tests.support.deterministic import (
    FakeAnalyst,
    FakeGoalNormalizer,
    FakePlanner,
    FakeReviewer,
    FakeWriter,
    analysis_provider,
    deliverables_provider,
    evidence_set,
    report_provider,
    research_evidence,
)


class _FixedClock:
    """A production-like clock: reading time does not advance it."""

    def __init__(self) -> None:
        self.current = datetime(2026, 7, 29, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class _FailOnceExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task, attempt, run) -> TaskExecutionOutcome:
        self.calls += 1
        if self.calls == 1:
            return TaskExecutionOutcome(succeeded=False, failure_code=FailureCode.TIMEOUT)
        return TaskExecutionOutcome(succeeded=True)


def _coordinator_with_executor(backend, executor) -> RunCoordinator:
    from agents_orchestration.orchestration.analysis_artifact import (
        SqliteAnalysisArtifactStore,
    )
    from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore

    limits = SystemLimits()
    artifact_store = SqliteAnalysisArtifactStore(
        SqliteArtifactStore(backend.conn, backend.artifact_dir)
    )
    return build_production_coordinator(
        backend,
        executor=executor,
        normalizer=FakeGoalNormalizer(),
        planner=FakePlanner(),
        analyst=FakeAnalyst(),
        writer=FakeWriter(),
        reviewer=FakeReviewer(),
        research_evidence=research_evidence,
        evidence_set=evidence_set,
        analysis_provider=analysis_provider,
        report_provider=report_provider,
        deliverables_provider=deliverables_provider,
        allowed_capabilities=frozenset(CapabilityKind),
        analysis_artifact_store=artifact_store,
        limits=limits,
    )

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


@pytest.mark.integration
async def test_drive_run_yields_during_fixed_clock_backoff_without_exhausting(tmp_path) -> None:
    """A production clock does not move just because drive_run polls.

    The runtime must yield while retry backoff is pending instead of consuming
    phase IDLE budget and terminating before a second Attempt can run.
    """

    clock = _FixedClock()
    backend = SqliteBackend(
        tmp_path / "waiting.sqlite",
        tmp_path / "waiting-artifacts",
        clock=clock,
    )
    executor = _FailOnceExecutor()
    coordinator = _coordinator_with_executor(backend, executor)
    service = OrchestrationService(
        backend,
        limits=SystemLimits(),
        run_policy=RunPolicy.from_limits(SystemLimits()),
        coordinator=coordinator,
    )
    try:
        run = service.create_run("research with retry", request_id="retry-wait")

        waiting = await service.drive_run(run.run_id)
        assert waiting.disposition is AdvanceDisposition.IDLE
        assert waiting.continue_immediately is False
        assert executor.calls == 1
        assert service.get_run(run.run_id).state is RunState.RESEARCHING

        still_waiting = await service.drive_run(run.run_id)
        assert still_waiting.disposition is AdvanceDisposition.IDLE
        assert executor.calls == 1
        assert service.get_run(run.run_id).state is RunState.RESEARCHING

        clock.advance(1)
        completed = await service.drive_run(run.run_id)
        assert completed.disposition is AdvanceDisposition.TERMINAL
        assert service.get_run(run.run_id).state is RunState.SUCCEEDED
        assert executor.calls > 1
    finally:
        backend.close()


@pytest.mark.integration
async def test_retry_releases_each_accepted_attempt_lease_and_survives_ttl(tmp_path) -> None:
    """Missing Lease release leaves two active epochs and makes Recovery fence
    the old epoch forever once its TTL expires."""

    clock = _FixedClock()
    backend = SqliteBackend(tmp_path / "leases.sqlite", tmp_path / "lease-artifacts", clock=clock)
    executor = _FailOnceExecutor()
    try:
        _seed(backend, clock, task_ids=("t1",))
        tick = RuntimeTick(backend, executor=executor, limits=SystemLimits())

        await tick.tick("r1")
        with backend.unit_of_work() as uow:
            assert uow.leases.active() == []
            assert uow.tasks.get("t1").state is TaskState.AWAITING_RETRY

        clock.advance(1)
        second = await tick.tick("r1")
        assert second.accepted == 1
        with backend.unit_of_work() as uow:
            assert uow.leases.active() == []
            assert uow.tasks.get("t1").state is TaskState.SUCCEEDED

        clock.advance(60)
        await tick.tick("r1")  # Recovery must not raise old-epoch ConcurrencyError.
    finally:
        backend.close()


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


@pytest.mark.integration
async def test_idle_without_fingerprint_is_recorded_and_bounded(backend) -> None:
    """Missing prerequisites often have no provider fingerprint; Coordinator
    must derive one instead of silently skipping the observation."""

    handler = _FakeHandler(
        PhaseId.PLAN,
        PhaseOutcome(
            AdvanceDisposition.IDLE,
            stage_logical_key="plan",
            reason="goal-or-contract-missing",
        ),
    )
    coord = RunCoordinator(backend, {PhaseId.PLAN: handler})
    run = _seed_coord(backend, RunState.PLANNING)

    await coord.advance(run.run_id)
    await coord.advance(run.run_id)
    third = await coord.advance(run.run_id)

    assert third.disposition is AdvanceDisposition.TERMINAL
    with backend.unit_of_work() as uow:
        stages = uow.stages.for_logical_stage(run.run_id, "plan")
        assert len(stages) == 3
        assert all(stage.fingerprint.state_version == run.state_version for stage in stages)


@pytest.mark.integration
async def test_only_current_consecutive_budgeted_idle_observations_are_counted(backend) -> None:
    """Historical fingerprints and BLOCKED observations must break, rather
    than contribute to, the current consecutive IDLE budget."""

    old = InputFingerprint(state_version=99)
    current = InputFingerprint(state_version=1)
    outcomes = iter(
        (
            PhaseOutcome(
                AdvanceDisposition.IDLE,
                input_fingerprint=old,
                stage_logical_key="goal",
            ),
            PhaseOutcome(
                AdvanceDisposition.BLOCKED,
                input_fingerprint=current,
                stage_logical_key="goal",
            ),
            PhaseOutcome(
                AdvanceDisposition.IDLE,
                input_fingerprint=current,
                stage_logical_key="goal",
            ),
            PhaseOutcome(
                AdvanceDisposition.IDLE,
                input_fingerprint=current,
                stage_logical_key="goal",
            ),
            PhaseOutcome(
                AdvanceDisposition.IDLE,
                input_fingerprint=current,
                stage_logical_key="goal",
            ),
        )
    )

    class _SequenceHandler:
        phase = PhaseId.GOAL

        async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
            return next(outcomes)

        def accept(self, outcome, run, uow, now):
            return run

    coord = RunCoordinator(backend, {PhaseId.GOAL: _SequenceHandler()})
    run = _seed_coord(backend, RunState.NORMALIZING)

    assert (await coord.advance(run.run_id)).disposition is AdvanceDisposition.IDLE
    assert (await coord.advance(run.run_id)).disposition is AdvanceDisposition.BLOCKED
    assert (await coord.advance(run.run_id)).disposition is AdvanceDisposition.IDLE
    assert (await coord.advance(run.run_id)).disposition is AdvanceDisposition.IDLE
    terminal = await coord.advance(run.run_id)
    assert terminal.disposition is AdvanceDisposition.TERMINAL


@pytest.mark.integration
async def test_stale_observation_does_not_consume_idle_budget(backend) -> None:
    """A stale provider result is audit evidence, not another phase failure."""

    class _DriftHandler:
        phase = PhaseId.GOAL

        async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
            with backend.unit_of_work() as uow:
                current = uow.runs.get(ctx.run.run_id)
                bumped = current.model_copy(
                    update={
                        "state_version": current.state_version + 1,
                        "updated_at": backend.clock.now(),
                    }
                )
                uow.runs.save(bumped, expected_version=current.state_version)
                uow.commit()
            return PhaseOutcome(
                AdvanceDisposition.PROGRESSED,
                next_state=RunState.PLANNING,
                input_fingerprint=InputFingerprint(state_version=ctx.run.state_version),
                stage_logical_key="goal",
            )

        def accept(self, outcome, run, uow, now):
            return run

    coord = RunCoordinator(backend, {PhaseId.GOAL: _DriftHandler()})
    run = _seed_coord(backend, RunState.NORMALIZING)

    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    with backend.unit_of_work() as uow:
        stages = uow.stages.for_logical_stage(run.run_id, "goal")
        assert len(stages) == 1
        assert stages[0].counts_toward_idle_budget is False

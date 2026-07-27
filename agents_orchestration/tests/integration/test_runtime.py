"""Integration tests for the Durable Runtime core (tasks 4.1-4.11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.domain.enums import (
    AttemptAcceptance,
    FailureCode,
    RunState,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.evidence import Usage
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.lifecycle import Lease, LeaseState
from agents_orchestration.domain.plan import Plan, PlanGraph
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.runtime.attempt import AttemptValidator
from agents_orchestration.runtime.core import BudgetGuard, RetryClassifier, Scheduler
from agents_orchestration.runtime.lease import LeaseManager
from agents_orchestration.runtime.recovery import RecoveryManager
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome
from agents_orchestration.runtime.watch import RuntimeWatch


def _seed(
    backend,
    clock,
    *,
    run_id="r1",
    task_ids=("t1", "t2"),
    depends_on=None,
    state=RunState.RESEARCHING,
    deadline_at=None,
    plan_version=1,
) -> Run:
    now = clock.now()
    policy = RunPolicy.from_limits(SystemLimits())
    budget = Budget(deadline_at=deadline_at) if deadline_at else Budget()
    run = Run(
        run_id=run_id,
        raw_goal="g",
        state=state,
        policy=policy,
        budget=budget,
        current_plan_version=plan_version,
        created_at=now,
        updated_at=now,
    )
    deps_map = depends_on or {}
    tasks = [
        Task(
            task_id=tid,
            run_id=run_id,
            plan_version=plan_version,
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            depth=1,
            depends_on=tuple(deps_map.get(tid, ())),
            created_at=now,
            updated_at=now,
        )
        for tid in task_ids
    ]
    plan = Plan(run_id=run_id, graph=PlanGraph(plan_id="p1", version=plan_version), proposed_at=now)
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.plans.save(plan)
        uow.tasks.materialize(tasks)
        uow.commit()
    return run


class _FakeExecutor:
    """Returns a configured outcome per task id; records call count."""

    def __init__(self, outcome_fn) -> None:
        self.outcome_fn = outcome_fn
        self.calls: list[str] = []

    async def execute(self, task: Task, attempt: Attempt, run: Run) -> TaskExecutionOutcome:
        self.calls.append(task.task_id)
        return self.outcome_fn(task)


def _succeed(_task: Task) -> TaskExecutionOutcome:
    return TaskExecutionOutcome(succeeded=True)


def _retryable_fail(_task: Task) -> TaskExecutionOutcome:
    return TaskExecutionOutcome(succeeded=False, failure_code=FailureCode.TIMEOUT)


# --- 4.1 Scheduler ----------------------------------------------------------


@pytest.mark.integration
def test_scheduler_ready_work_respects_dependencies(backend, fake_clock) -> None:
    _seed(backend, fake_clock, task_ids=("a", "b"), depends_on={"b": ("a",)})
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        ready = Scheduler(uow).ready_work(run, max_concurrency=4)
    assert [t.task_id for t in ready] == ["a"]


@pytest.mark.integration
def test_scheduler_includes_already_ready_tasks(backend, fake_clock) -> None:
    _seed(backend, fake_clock, task_ids=("a", "b"))
    with backend.unit_of_work() as uow:
        uow.tasks.save(uow.tasks.get("a").transition(TaskState.READY, fake_clock.now()))
        uow.commit()
    with backend.unit_of_work() as uow:
        ready = Scheduler(uow).ready_work(uow.runs.get("r1"), max_concurrency=4)
    assert {t.task_id for t in ready} == {"a", "b"}


# --- 4.3 BudgetGuard --------------------------------------------------------


@pytest.mark.integration
def test_budget_guard_denies_deadline_and_replan_overrun() -> None:
    limits = SystemLimits()
    guard = BudgetGuard(limits)
    now = datetime(2026, 7, 27, tzinfo=UTC)
    past = Budget(deadline_at=now - timedelta(seconds=1))
    policy = RunPolicy.from_limits(limits)
    over_deadline = Run(
        run_id="r",
        raw_goal="g",
        policy=policy,
        budget=past,
        created_at=now,
        updated_at=now,
    )
    assert not guard.check_run(over_deadline, now=now, dispatched_count=0).allowed

    over_replan = over_deadline.model_copy(
        update={"replan_count": limits.max_replans + 1, "budget": Budget()}
    )
    decision = guard.check_run(over_replan, now=now, dispatched_count=0)
    assert not decision.allowed
    assert any("replan" in v for v in decision.violations)


# --- 4.4 RetryClassifier ----------------------------------------------------


@pytest.mark.integration
def test_retry_classifier_bounds_attempts_and_skips_non_retryable() -> None:
    clf = RetryClassifier(SystemLimits(), base_backoff_seconds=2.0)
    assert clf.classify(failure_code=FailureCode.TIMEOUT, attempts_used=1, max_attempts=3).retry
    backoff = clf.classify(failure_code=FailureCode.TIMEOUT, attempts_used=2, max_attempts=3)
    assert backoff.backoff_seconds == 4.0
    assert not clf.classify(failure_code=FailureCode.TIMEOUT, attempts_used=3, max_attempts=3).retry
    assert not clf.classify(
        failure_code=FailureCode.UNAUTHORIZED, attempts_used=1, max_attempts=3
    ).retry


# --- 4.2 / 4.6 Lease + Recovery --------------------------------------------


@pytest.mark.integration
def test_lease_manager_monotonic_epoch(backend, fake_clock) -> None:
    with backend.unit_of_work() as uow:
        mgr = LeaseManager(uow, fake_clock, backend.idgen, lease_ttl_seconds=10)
        l1 = mgr.claim(task_id="t1", attempt_id="a1", run_id="r1")
        l2 = mgr.claim(task_id="t1", attempt_id="a2", run_id="r1")
        uow.commit()
        assert l2.epoch == l1.epoch + 1


@pytest.mark.integration
def test_recovery_requeues_dispatched_task_with_expired_lease(backend, fake_clock) -> None:
    now = fake_clock.now()
    _seed(backend, fake_clock, task_ids=("t1",))
    # Simulate a dispatch that died: task DISPATCHED + lease already expired.
    with backend.unit_of_work() as uow:
        task = uow.tasks.get("t1").transition(TaskState.DISPATCHED, now, attempt_count=1)
        uow.tasks.save(task)
        uow.attempts.save(
            Attempt(
                attempt_id="a1",
                task_id="t1",
                run_id="r1",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                lease_epoch=1,
                plan_version=1,
                state_version_at_dispatch=1,
                started_at=now,
            )
        )
        uow.leases.save(
            Lease(
                task_id="t1",
                attempt_id="a1",
                run_id="r1",
                epoch=1,
                claimed_at=now,
                expires_at=now,  # already expired relative to a later clock tick
            )
        )
        uow.commit()

    # Advance the clock so the lease is stale, then recover.
    fake_clock.advance(60)
    with backend.unit_of_work() as uow:
        mgr = LeaseManager(uow, fake_clock, backend.idgen)
        report = RecoveryManager(uow, mgr, fake_clock).recover("r1", now=fake_clock.now())
        uow.commit()
    assert "t1" in report.requeued_tasks
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.PENDING


# --- 4.7 / 4.8 Attempt validation + Late Result ----------------------------


@pytest.mark.integration
def test_attempt_validator_rejects_stale_lease(backend, fake_clock) -> None:
    now = fake_clock.now()
    _seed(backend, fake_clock, task_ids=("t1",))
    with backend.unit_of_work() as uow:
        task = uow.tasks.get("t1").transition(TaskState.DISPATCHED, now)
        uow.tasks.save(task)
        attempt = Attempt(
            attempt_id="a1",
            task_id="t1",
            run_id="r1",
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            lease_epoch=1,
            plan_version=1,
            state_version_at_dispatch=1,
            started_at=now,
        )
        uow.attempts.save(attempt)
        # A newer epoch was claimed; the old epoch's result must be fenced.
        uow.leases.save(
            Lease(
                task_id="t1",
                attempt_id="a2",
                run_id="r1",
                epoch=2,
                claimed_at=now,
                expires_at=now + timedelta(seconds=30),
                state=LeaseState.CLAIMED,
            )
        )
        uow.commit()
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        verdict = AttemptValidator(uow).validate(attempt, run=run, current_plan_version=1)
    assert verdict is AttemptAcceptance.REJECTED_STALE_LEASE


# --- 4.9 Tick ---------------------------------------------------------------


@pytest.mark.integration
async def test_tick_dispatches_and_accepts_success(backend, fake_clock) -> None:
    _seed(backend, fake_clock, task_ids=("t1",))
    tick = RuntimeTick(backend, executor=_FakeExecutor(_succeed), limits=SystemLimits())
    report = await tick.tick("r1")
    assert report.accepted == 1 and report.dispatched == 1
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.SUCCEEDED
        assert uow.attempts.active_for_task("t1") is None  # no longer dispatched


@pytest.mark.integration
async def test_tick_retries_then_fails_after_attempts_exhausted(backend, fake_clock) -> None:
    _seed(backend, fake_clock, task_ids=("t1",))
    tick = RuntimeTick(backend, executor=_FakeExecutor(_retryable_fail), limits=SystemLimits())
    states: list[TaskState] = []
    # max_attempts_per_task default is 3: attempts 1 and 2 retry, attempt 3 fails.
    for _ in range(3):
        await tick.tick("r1")
        with backend.unit_of_work() as uow:
            task = uow.tasks.get("t1")
            states.append(task.state)
            if task.state is TaskState.AWAITING_RETRY:
                uow.tasks.save(task.transition(TaskState.READY, fake_clock.now()))
                uow.commit()
    assert states[0] is TaskState.AWAITING_RETRY
    assert states[-1] is TaskState.FAILED


@pytest.mark.integration
async def test_tick_terminates_on_deadline(backend, fake_clock) -> None:
    _seed(
        backend,
        fake_clock,
        task_ids=("t1",),
        deadline_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    tick = RuntimeTick(backend, executor=_FakeExecutor(_succeed), limits=SystemLimits())
    report = await tick.tick("r1")
    assert report.terminal and report.termination is TerminationReason.DEADLINE_EXCEEDED
    with backend.unit_of_work() as uow:
        assert uow.runs.get("r1").state is RunState.FAILED


# --- 4.10 Watch -------------------------------------------------------------


@pytest.mark.integration
async def test_watch_drives_until_blocked_with_all_succeeded(backend, fake_clock) -> None:
    _seed(backend, fake_clock, task_ids=("t1", "t2"))
    tick = RuntimeTick(backend, executor=_FakeExecutor(_succeed), limits=SystemLimits())
    watch = RuntimeWatch(backend, tick)
    report = await watch.drive_run("r1", max_ticks=10)
    assert report.blocked
    with backend.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.SUCCEEDED
        assert uow.tasks.get("t2").state is TaskState.SUCCEEDED


# --- 4.11 Restart across a fresh backend -----------------------------------


@pytest.mark.integration
async def test_restart_recovers_and_completes_in_fresh_process(tmp_path, fake_clock) -> None:
    from agents_orchestration.runtime.persistence.connection import SqliteBackend

    db = tmp_path / "runtime.sqlite"
    arts = tmp_path / "artifacts"
    backend_a = SqliteBackend(db, arts, clock=fake_clock)
    _seed(backend_a, fake_clock, task_ids=("t1",))
    # Simulate mid-dispatch crash: task DISPATCHED, lease expired.
    now = fake_clock.now()
    with backend_a.unit_of_work() as uow:
        task = uow.tasks.get("t1").transition(TaskState.DISPATCHED, now, attempt_count=1)
        uow.tasks.save(task)
        uow.attempts.save(
            Attempt(
                attempt_id="a1",
                task_id="t1",
                run_id="r1",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                lease_epoch=1,
                plan_version=1,
                state_version_at_dispatch=1,
                started_at=now,
            )
        )
        uow.leases.save(
            Lease(
                task_id="t1",
                attempt_id="a1",
                run_id="r1",
                epoch=1,
                claimed_at=now,
                expires_at=now,
            )
        )
        uow.commit()
    backend_a.close()

    # Fresh process opens the same store and resumes.
    backend_b = SqliteBackend(db, arts, clock=fake_clock)
    fake_clock.advance(60)
    tick = RuntimeTick(backend_b, executor=_FakeExecutor(_succeed), limits=SystemLimits())
    await tick.tick("r1")
    with backend_b.unit_of_work() as uow:
        assert uow.tasks.get("t1").state is TaskState.SUCCEEDED
    backend_b.close()


@pytest.mark.integration
async def test_tick_consumes_usage_budget(backend, fake_clock) -> None:
    _seed(backend, fake_clock, task_ids=("t1",))

    def costly(_task: Task) -> TaskExecutionOutcome:
        return TaskExecutionOutcome(succeeded=True, usage=Usage(tokens=50))

    tick = RuntimeTick(backend, executor=_FakeExecutor(costly), limits=SystemLimits())
    await tick.tick("r1")
    with backend.unit_of_work() as uow:
        assert uow.runs.get("r1").budget.tokens_used == 50

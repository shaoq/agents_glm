"""One bounded Runtime Tick for an explicitly selected Run (task 4.9).

The Tick is the durable execution unit (design Decision 5). It runs in two
phases so capability calls never execute inside a write transaction:

1. Dispatch transaction — recover, compute Ready Work, claim leases, materialize
   Attempts, transition Tasks to DISPATCHED, emit events, checkpoint, commit.
2. Execute — await the injected :class:`TaskExecutor` for each dispatch (outside
   any transaction).
3. Accept transaction — validate each result (fencing), accept or reject, retry
   or fail, record artifacts/evidence, checkpoint, commit.

A single Tick is bounded (one pass over currently-ready work). :class:`RuntimeWatch`
(4.10) loops Ticks until the Run is terminal or blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agents_orchestration.domain.enums import (
    AttemptAcceptance,
    AttemptState,
    EffectType,
    FailureCode,
    RunState,
    TaskState,
    TerminationReason,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.evidence import Usage
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.lifecycle import CheckpointKind
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.runtime.attempt import AttemptValidator, record_late_observation
from agents_orchestration.runtime.core import (
    BudgetGuard,
    BudgetOverrun,
    CheckpointService,
    RetryClassifier,
    Scheduler,
)
from agents_orchestration.runtime.lease import LeaseManager
from agents_orchestration.runtime.recovery import RecoveryManager


@dataclass(frozen=True)
class TaskExecutionOutcome:
    succeeded: bool
    task_result: TaskResult | None = None
    failure_code: FailureCode | None = None
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class TaskExecutor(Protocol):
    """Executes one Attempt outside any write transaction (Section 6 impl)."""

    async def execute(self, task: Task, attempt: Attempt, run: Run) -> TaskExecutionOutcome: ...


@dataclass(frozen=True)
class TickReport:
    run_id: str
    dispatched: int = 0
    accepted: int = 0
    retried: int = 0
    failed: int = 0
    rejected_late: int = 0
    terminal: bool = False
    blocked: bool = False
    termination: TerminationReason | None = None
    violations: tuple[str, ...] = field(default_factory=tuple)


class RuntimeTick:
    def __init__(
        self,
        backend,
        *,
        executor: TaskExecutor,
        limits: SystemLimits,
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        self.backend = backend
        self.executor = executor
        self.limits = limits
        self.lease_ttl_seconds = lease_ttl_seconds

    async def tick(self, run_id: str) -> TickReport:
        clock = self.backend.clock
        idgen = self.backend.idgen
        now = clock.now()

        # Phase 1: dispatch transaction.
        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.is_terminal:
                return TickReport(run_id, terminal=True, termination=run.termination)
            if run.state is RunState.PAUSED or run.state.is_gate_waiting:
                return TickReport(run_id, blocked=True)

            lease_manager = LeaseManager(
                uow, clock, idgen, lease_ttl_seconds=self.lease_ttl_seconds
            )
            RecoveryManager(uow, lease_manager, clock).recover(run.run_id, now=now)

            guard = BudgetGuard(self.limits)
            decision = guard.check_run(run, now=now, dispatched_count=0)
            if not decision.allowed:
                return self._terminate(uow, run, decision.violations, now)

            scheduler = Scheduler(uow)
            ready = scheduler.ready_work(run, max_concurrency=run.policy.max_concurrency)
            if not ready:
                return TickReport(run_id, blocked=self._has_in_flight(uow, run.run_id))

            dispatches: list[tuple[Task, Attempt]] = []
            checkpoints = CheckpointService(uow, clock, idgen)
            for task in ready:
                attempt_id = idgen.new_id("att")
                lease = lease_manager.claim(
                    task_id=task.task_id, attempt_id=attempt_id, run_id=run.run_id
                )
                attempt = Attempt(
                    attempt_id=attempt_id,
                    task_id=task.task_id,
                    run_id=run.run_id,
                    worker_role=task.worker_role,
                    lease_epoch=lease.epoch,
                    plan_version=run.current_plan_version or 1,
                    state_version_at_dispatch=run.state_version,
                    started_at=now,
                )
                uow.attempts.save(attempt)
                dispatched_task = task.transition(
                    TaskState.DISPATCHED, now, attempt_count=task.attempt_count + 1
                )
                uow.tasks.save(dispatched_task)
                uow.events.append(
                    [self._event(run, EffectType.TASK_DISPATCHED, now, task=task, attempt=attempt)]
                )
                dispatches.append((dispatched_task, attempt))
            checkpoints.record(
                run_id=run.run_id,
                kind=CheckpointKind.BRANCH_RESULT,
                state_version=run.state_version,
                plan_version=run.current_plan_version,
                reason=f"dispatched {len(dispatches)} task(s)",
            )
            uow.commit()

        # Phase 2: execute outside any transaction.
        run_snapshot = run
        outcomes = []
        for task, attempt in dispatches:
            outcome = await self.executor.execute(task, attempt, run_snapshot)
            outcomes.append((task, attempt, outcome))

        # Phase 3: accept transaction.
        return await self._accept(run_id, outcomes)

    async def _accept(self, run_id: str, outcomes) -> TickReport:
        clock = self.backend.clock
        idgen = self.backend.idgen
        now = clock.now()
        accepted = retried = failed = rejected = 0

        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            expected_version = run.state_version
            validator = AttemptValidator(uow)
            classifier = RetryClassifier(self.limits)
            checkpoints = CheckpointService(uow, clock, idgen)

            for task, attempt, outcome in outcomes:
                fetched_attempt = uow.attempts.get(attempt.attempt_id)
                if fetched_attempt is None:
                    continue
                acceptance = validator.validate(
                    fetched_attempt,
                    run=run,
                    current_plan_version=run.current_plan_version or 1,
                )
                # Consume reported usage against the shared budget (best-effort).
                run = self._consume_usage(run, outcome.usage)

                if acceptance is AttemptAcceptance.ACCEPTED and outcome.succeeded:
                    self._accept_success(uow, run, task, fetched_attempt, outcome, now, checkpoints)
                    accepted += 1
                elif acceptance is AttemptAcceptance.ACCEPTED and not outcome.succeeded:
                    retried_or_failed = self._accept_failure(
                        uow, run, task, fetched_attempt, outcome, now, classifier, checkpoints
                    )
                    if retried_or_failed == "retried":
                        retried += 1
                    else:
                        failed += 1
                else:
                    record_late_observation(
                        uow,
                        attempt=fetched_attempt,
                        acceptance=acceptance,
                        state_version=run.state_version,
                        clock=clock,
                        idgen=idgen,
                    )
                    uow.attempts.save(
                        fetched_attempt.model_copy(
                            update={
                                "state": AttemptState.REJECTED,
                                "acceptance": acceptance,
                                "finished_at": now,
                            }
                        )
                    )
                    rejected += 1

            checkpoints.record(
                run_id=run.run_id,
                kind=CheckpointKind.BRANCH_RESULT,
                state_version=run.state_version,
                plan_version=run.current_plan_version,
                reason=f"accepted {accepted} retried {retried} failed {failed} rejected {rejected}",
            )
            uow.runs.save(run, expected_version=expected_version)
            uow.commit()

        return TickReport(
            run_id,
            dispatched=len(outcomes),
            accepted=accepted,
            retried=retried,
            failed=failed,
            rejected_late=rejected,
            terminal=run.is_terminal,
        )

    def _accept_success(self, uow, run, task, attempt, outcome, now, checkpoints) -> None:
        result = outcome.task_result
        result_ref = result.artifacts[0] if result and result.artifacts else None
        for artifact in result.artifacts if result else ():
            uow.artifacts.record_metadata(artifact)
        uow.attempts.save(
            attempt.succeed(result_ref, now)
            if result_ref
            else attempt.model_copy(
                update={
                    "state": AttemptState.SUCCEEDED,
                    "acceptance": AttemptAcceptance.ACCEPTED,
                    "finished_at": now,
                }
            )
        )
        current = uow.tasks.get(task.task_id)
        uow.tasks.save(
            current.transition(TaskState.SUCCEEDED, now, accepted_attempt_id=attempt.attempt_id)
        )
        uow.events.append(
            [self._event(run, EffectType.ATTEMPT_ACCEPTED, now, task=task, attempt=attempt)]
        )

    def _accept_failure(
        self, uow, run, task, attempt, outcome, now, classifier, checkpoints
    ) -> str:
        code = outcome.failure_code or FailureCode.UNKNOWN
        decision = classifier.classify(
            failure_code=code,
            attempts_used=task.attempt_count,
            max_attempts=run.policy.max_attempts_per_task,
        )
        uow.attempts.save(attempt.fail(code, now, acceptance=AttemptAcceptance.ACCEPTED))
        current = uow.tasks.get(task.task_id)
        if decision.retry:
            uow.tasks.save(current.transition(TaskState.AWAITING_RETRY, now))
            checkpoints.record(
                run_id=run.run_id,
                kind=CheckpointKind.RETRY,
                state_version=run.state_version,
                plan_version=run.current_plan_version,
                reason=(
                    f"retry {task.task_id} after {code.value} "
                    f"backoff={decision.backoff_seconds}s"
                ),
            )
            uow.events.append(
                [self._event(run, EffectType.ATTEMPT_REJECTED, now, task=task, attempt=attempt)]
            )
            return "retried"
        uow.tasks.save(current.transition(TaskState.FAILED, now, failure_code=code))
        uow.events.append(
            [self._event(run, EffectType.TASK_STATE_TRANSITION, now, task=task, attempt=attempt)]
        )
        return "failed"

    def _consume_usage(self, run, usage: Usage):
        from agents_orchestration.runtime.core import consume_budget_safe

        try:
            return consume_budget_safe(run, tokens=usage.tokens, cost_usd=usage.cost_usd)
        except BudgetOverrun:
            return run.terminate(TerminationReason.BUDGET_EXCEEDED, self.backend.clock.now())

    def _terminate(self, uow, run, violations, now) -> TickReport:
        reason = (
            TerminationReason.DEADLINE_EXCEEDED
            if "deadline_exceeded" in violations
            else TerminationReason.BUDGET_EXCEEDED
            if any("budget" in v for v in violations)
            else TerminationReason.FAILED
        )
        terminated = run.terminate(reason, now)
        uow.runs.save(terminated, expected_version=run.state_version)
        uow.events.append([self._event(terminated, EffectType.RUN_TERMINATED, now)])
        uow.commit()
        return TickReport(
            run.run_id, terminal=True, termination=reason, violations=tuple(violations)
        )

    def _has_in_flight(self, uow, run_id: str) -> bool:
        return any(
            t.state in (TaskState.DISPATCHED, TaskState.AWAITING_RETRY)
            for t in uow.tasks.by_run(run_id)
        )

    def _event(
        self,
        run: Run,
        effect: EffectType,
        at,
        *,
        task: Task | None = None,
        attempt: Attempt | None = None,
    ) -> DomainEvent:
        return DomainEvent(
            event_id=self.backend.idgen.new_id("evt"),
            run_id=run.run_id,
            effect=effect,
            state_version=run.state_version,
            occurred_at=at,
            task_id=task.task_id if task else None,
            attempt_id=attempt.attempt_id if attempt else None,
            plan_version=run.current_plan_version,
        )

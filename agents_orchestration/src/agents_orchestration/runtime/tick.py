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

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from agents_orchestration.domain.capability import CapabilityResult
from agents_orchestration.domain.coordination import eligible_worker_roles
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
from agents_orchestration.domain.execution import Attempt, Operation, OutcomeCertainty, Run, Task
from agents_orchestration.domain.lifecycle import CheckpointKind
from agents_orchestration.domain.plan import (
    Plan,
    ResearchExecutionMode,
    SeedExplorationBoundary,
)
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.domain.research_loop import (
    AddDirectionAction,
    QueryAction,
    ResearchDirection,
    ResearchLoop,
    ResearchLoopStatus,
    ResearchStep,
    ResearchStepStatus,
    StopRequestAction,
)
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.orchestration.research_agent_loop import (
    ActionValidationError,
    LoopGuard,
    ResearchAgentLoopExecutor,
    ResearchDecisionOutcome,
)
from agents_orchestration.runtime.attempt import AttemptValidator, record_late_observation
from agents_orchestration.runtime.core import (
    BudgetGuard,
    BudgetOverrun,
    CheckpointService,
    RetryClassifier,
    Scheduler,
    retry_backoff_seconds,
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


@dataclass(frozen=True)
class _Dispatch:
    task: Task
    attempt: Attempt
    mode: ResearchExecutionMode
    loop: ResearchLoop | None = None
    step: ResearchStep | None = None
    seed_boundary: SeedExplorationBoundary | None = None
    allowed_capabilities: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class _AgentLoopOutcome:
    succeeded: bool
    decision: ResearchDecisionOutcome | None = None
    capability_result: CapabilityResult | None = None
    failure_code: FailureCode | None = None
    diagnostic: str | None = None
    fenced: bool = False


class RuntimeTick:
    def __init__(
        self,
        backend,
        *,
        executor: TaskExecutor,
        agent_loop_executor: ResearchAgentLoopExecutor | None = None,
        limits: SystemLimits,
        lease_ttl_seconds: float = 30.0,
        base_backoff_seconds: float = 1.0,
        reasoning_reservation_tokens: int = 0,
    ) -> None:
        self.backend = backend
        self.executor = executor
        self.agent_loop_executor = agent_loop_executor
        self.limits = limits
        self.lease_ttl_seconds = lease_ttl_seconds
        self.base_backoff_seconds = base_backoff_seconds
        self.reasoning_reservation_tokens = reasoning_reservation_tokens

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

            if uow.gates.open_for_run(run.run_id):
                # A version-bound Human Gate holds the Run; resume creates a new
                # Attempt/Lease after the Gate is consumed (task 9.6).
                return TickReport(run_id, blocked=True)

            self._readmit_retry_ready(uow, run, now)

            # Hard state guard (remove-noop-phase-tasks 2.1): only RESEARCHING
            # schedules Tasks. Every other RunState produces zero dispatches even
            # when ready or legacy Tasks exist; eligible_worker_roles is NOT used
            # as an implicit "dispatch nothing" signal.
            if run.state is not RunState.RESEARCHING:
                return TickReport(run_id, blocked=self._has_in_flight(uow, run.run_id))

            scheduler = Scheduler(uow)
            ready = scheduler.ready_work(run, max_concurrency=run.policy.max_concurrency)
            eligible = eligible_worker_roles(run.state)
            if eligible is not None:
                # RESEARCHING only: filter to the phase's eligible roles.
                ready = [t for t in ready if t.worker_role in eligible]
            if not ready:
                return TickReport(run_id, blocked=self._has_in_flight(uow, run.run_id))

            plan = uow.plans.get(run.run_id, run.current_plan_version or 1)
            if (
                plan is not None
                and plan.graph.research_execution_mode is ResearchExecutionMode.AGENT_LOOP
                and self.reasoning_reservation_tokens > 0
                and run.budget.max_tokens is not None
                and (
                    run.budget.max_tokens - run.budget.tokens_used
                    < self.reasoning_reservation_tokens * len(ready)
                )
            ):
                return self._terminate(
                    uow,
                    run,
                    ("token_budget_reservation_exceeded",),
                    now,
                )

            dispatches: list[_Dispatch] = []
            checkpoints = CheckpointService(uow, clock, idgen)
            for task in ready:
                mode = (
                    plan.graph.research_execution_mode
                    if plan is not None
                    else ResearchExecutionMode.FIXED_FANOUT
                )
                loop: ResearchLoop | None = None
                step: ResearchStep | None = None
                seed_boundary: SeedExplorationBoundary | None = None
                if mode is ResearchExecutionMode.AGENT_LOOP:
                    if self.agent_loop_executor is None:
                        raise RuntimeError("agent_loop Plan requires a ResearchAgentLoopExecutor")
                    boundary = plan.graph.exploration_boundary
                    seed_boundary = boundary.for_seed(task.task_id) if boundary else None
                    if boundary is None or seed_boundary is None:
                        raise RuntimeError(
                            f"agent_loop Plan has no boundary for seed {task.task_id}"
                        )
                    loop = self._ensure_loop(uow, run, plan, task, now)
                    if self._exhausted_before_dispatch(loop, seed_boundary):
                        self._close_exhausted_loop(
                            uow,
                            run,
                            task,
                            loop,
                            now,
                            reason=self._loop_exhaustion_reason(loop, seed_boundary),
                        )
                        continue

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
                if mode is ResearchExecutionMode.AGENT_LOOP:
                    step = self._prepare_deciding_step(
                        uow,
                        run,
                        task,
                        attempt,
                        lease.epoch,
                        loop,
                        now,
                    )
                dispatched_task = task.transition(
                    TaskState.DISPATCHED, now, attempt_count=task.attempt_count + 1
                )
                uow.tasks.save(dispatched_task)
                uow.events.append(
                    [self._event(run, EffectType.TASK_DISPATCHED, now, task=task, attempt=attempt)]
                )
                dispatches.append(
                    _Dispatch(
                        task=dispatched_task,
                        attempt=attempt,
                        mode=mode,
                        loop=loop,
                        step=step,
                        seed_boundary=seed_boundary,
                        allowed_capabilities=(
                            frozenset(plan.graph.exploration_boundary.allowed_capabilities)
                            if plan is not None and plan.graph.exploration_boundary
                            else frozenset()
                        ),
                    )
                )
            checkpoints.record(
                run_id=run.run_id,
                kind=CheckpointKind.BRANCH_RESULT,
                state_version=run.state_version,
                plan_version=run.current_plan_version,
                reason=f"dispatched {len(dispatches)} task(s)",
            )
            uow.commit()

        if not dispatches:
            return TickReport(run_id, blocked=False)

        # Phase 2: execute outside any transaction — dispatches run concurrently
        # up to ``run.policy.max_concurrency``. The Semaphore makes the existing
        # max_concurrency field (already used for scheduling, ready_work) also
        # govern execution, so multi-source / multi-task research fans out for
        # real. Each Task already has its own Lease (claimed in Phase 1), and
        # Phase 3 accepts outcomes as a batch, so concurrency is recovery-safe.
        run_snapshot = run
        sem = asyncio.Semaphore(run_snapshot.policy.max_concurrency)

        async def _execute_one(dispatch: _Dispatch):
            async with sem:
                if dispatch.mode is ResearchExecutionMode.AGENT_LOOP:
                    outcome = await self._execute_agent_dispatch(dispatch, run_snapshot)
                else:
                    outcome = await self.executor.execute(
                        dispatch.task, dispatch.attempt, run_snapshot
                    )
                return dispatch, outcome

        outcomes = await asyncio.gather(*(_execute_one(dispatch) for dispatch in dispatches))

        # Phase 3: accept transaction.
        return await self._accept(run_id, outcomes)

    def _ensure_loop(self, uow, run: Run, plan: Plan, task: Task, now) -> ResearchLoop:
        existing = uow.research_loops.for_task(run.run_id, plan.version, task.task_id)
        if existing is not None:
            return existing
        loop_id = self._stable_id("loop", run.run_id, str(plan.version), task.task_id)
        loop = ResearchLoop(
            loop_id=loop_id,
            run_id=run.run_id,
            plan_version=plan.version,
            task_id=task.task_id,
            created_at=now,
            updated_at=now,
        )
        boundary = plan.graph.exploration_boundary
        if boundary is None or self.agent_loop_executor is None:
            raise RuntimeError("agent_loop boundary/executor missing")
        prepared = self.agent_loop_executor.direction_policy.prepare(
            task.description or task.task_id,
            approved_capabilities=boundary.allowed_capabilities,
        )
        direction = ResearchDirection(
            direction_id=self._stable_id("direction", loop_id, "seed"),
            loop_id=loop_id,
            run_id=run.run_id,
            plan_version=plan.version,
            task_id=task.task_id,
            text=prepared.text,
            focus_hash=prepared.focus_hash,
            capability_scope=prepared.capability_scope,
            source_step_id=None,
            created_at=now,
        )
        uow.research_loops.save(loop, expected_version=None)
        uow.research_directions.save(direction)
        uow.events.append(
            [
                self._event(
                    run,
                    EffectType.RESEARCH_LOOP_STARTED,
                    now,
                    task=task,
                    payload={"loop_id": loop.loop_id},
                )
            ]
        )
        return loop

    def _prepare_deciding_step(
        self,
        uow,
        run: Run,
        task: Task,
        attempt: Attempt,
        lease_epoch: int,
        loop: ResearchLoop,
        now,
    ) -> ResearchStep:
        existing = uow.research_steps.by_logical_key(
            run.run_id, loop.plan_version, task.task_id, loop.next_step_index
        )
        if existing is None:
            step_id = self._stable_id(
                "step",
                run.run_id,
                str(loop.plan_version),
                task.task_id,
                str(loop.next_step_index),
            )
            step = ResearchStep(
                step_id=step_id,
                loop_id=loop.loop_id,
                run_id=run.run_id,
                plan_version=loop.plan_version,
                task_id=task.task_id,
                step_index=loop.next_step_index,
                status=ResearchStepStatus.DECIDING,
                decision_request_id=f"decision:{step_id}",
                capability_request_id=f"capability:{step_id}",
                attempt_id=attempt.attempt_id,
                lease_epoch=lease_epoch,
                state_version_at_dispatch=run.state_version,
                reasoning_reservation=Usage(tokens=self.reasoning_reservation_tokens),
                created_at=now,
                updated_at=now,
            )
            uow.research_steps.save(step, expected_status=None)
            uow.dedup.try_claim(
                step.decision_request_id, run_id=run.run_id, kind="research_agent_decision"
            )
            return step

        if existing.status is ResearchStepStatus.ACCEPTED:
            raise RuntimeError(f"accepted logical step replayed: {existing.step_id}")
        status = (
            ResearchStepStatus.PREPARED
            if existing.action is not None
            and existing.status in {ResearchStepStatus.PREPARED, ResearchStepStatus.FAILED}
            else ResearchStepStatus.DECIDING
        )
        step = existing.model_copy(
            update={
                "status": status,
                "attempt_id": attempt.attempt_id,
                "lease_epoch": lease_epoch,
                "state_version_at_dispatch": run.state_version,
                "failure_code": None,
                "updated_at": now,
            }
        )
        uow.research_steps.save(step, expected_status=existing.status)
        return step

    def _exhausted_before_dispatch(
        self, loop: ResearchLoop, boundary: SeedExplorationBoundary
    ) -> bool:
        if loop.step_count >= boundary.max_steps:
            return True
        if boundary.max_tokens is not None:
            remaining = boundary.max_tokens - loop.usage.tokens
            if remaining < self.reasoning_reservation_tokens:
                return True
        if boundary.max_cost_usd is not None and loop.usage.cost_usd >= boundary.max_cost_usd:
            return True
        return False

    def _loop_exhaustion_reason(self, loop: ResearchLoop, boundary: SeedExplorationBoundary) -> str:
        if loop.step_count >= boundary.max_steps:
            return "max_steps_exhausted"
        return "loop_budget_exhausted"

    def _close_exhausted_loop(
        self, uow, run: Run, task: Task, loop: ResearchLoop, now, *, reason: str
    ) -> None:
        if loop.status.is_closed:
            return
        closed = loop.model_copy(
            update={
                "status": ResearchLoopStatus.EXHAUSTED,
                "degradation_reason": reason,
                "state_version": loop.state_version + 1,
                "updated_at": now,
            }
        )
        uow.research_loops.save(closed, expected_version=loop.state_version)
        current = uow.tasks.get(task.task_id)
        if current is not None and not current.is_terminal:
            uow.tasks.save(current.transition(TaskState.SUCCEEDED, now))
        uow.events.append(
            [
                self._event(
                    run,
                    EffectType.RESEARCH_LOOP_EXHAUSTED,
                    now,
                    task=task,
                    payload={"loop_id": loop.loop_id, "reason": reason},
                )
            ]
        )

    async def _execute_agent_dispatch(
        self, dispatch: _Dispatch, run_snapshot: Run
    ) -> _AgentLoopOutcome:
        if (
            self.agent_loop_executor is None
            or dispatch.loop is None
            or dispatch.step is None
            or dispatch.seed_boundary is None
        ):
            return _AgentLoopOutcome(False, failure_code=FailureCode.UNKNOWN)

        step = dispatch.step
        if step.status is ResearchStepStatus.PREPARED and step.action is not None:
            decision = ResearchDecisionOutcome(envelope=step.action, usage=step.reasoning_usage)
            prepared_run = run_snapshot
            prepared_step = step
        else:
            with self.backend.unit_of_work() as uow:
                directions = tuple(uow.research_directions.by_loop(dispatch.loop.loop_id))
                evidence = tuple(uow.evidence.by_run(run_snapshot.run_id))
                uow.commit()
            try:
                decision, heartbeat_ok = await self._with_lease_heartbeat(
                    dispatch,
                    self.agent_loop_executor.decide(
                        run=run_snapshot,
                        task=dispatch.task,
                        loop=dispatch.loop,
                        step=step,
                        seed_boundary=dispatch.seed_boundary,
                        allowed_capabilities=dispatch.allowed_capabilities,
                        directions=directions,
                        evidence=evidence,
                    ),
                )
            except ActionValidationError as exc:
                return _AgentLoopOutcome(False, failure_code=exc.failure_code, diagnostic=str(exc))
            except ValidationError as exc:
                return _AgentLoopOutcome(
                    False,
                    failure_code=FailureCode.INVALID_RESPONSE,
                    diagnostic=f"typed_validation_failed:{exc.error_count()}",
                )
            except Exception as exc:  # noqa: BLE001 - provider failures are structured
                return _AgentLoopOutcome(
                    False,
                    failure_code=FailureCode.UPSTREAM_ERROR,
                    diagnostic=type(exc).__name__,
                )
            if not heartbeat_ok:
                return _AgentLoopOutcome(
                    False, decision=decision, failure_code=FailureCode.TIMEOUT, fenced=True
                )
            prepared = self._persist_prepared_action(dispatch, decision)
            if prepared is None:
                return _AgentLoopOutcome(
                    False, decision=decision, failure_code=FailureCode.UNKNOWN, fenced=True
                )
            prepared_step, prepared_run = prepared
            if prepared_run.is_terminal:
                return _AgentLoopOutcome(
                    False,
                    decision=decision,
                    failure_code=FailureCode.BUDGET_EXCEEDED,
                )
            if prepared_step.failure_code == FailureCode.BUDGET_EXCEEDED.value:
                return _AgentLoopOutcome(
                    False,
                    decision=decision,
                    failure_code=FailureCode.BUDGET_EXCEEDED,
                    diagnostic="loop_budget_reservation_exceeded",
                )

        try:
            result, heartbeat_ok = await self._with_lease_heartbeat(
                dispatch,
                self.agent_loop_executor.execute_action(
                    decision=decision,
                    step=prepared_step,
                    task=dispatch.task,
                    attempt=dispatch.attempt,
                    run=prepared_run,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - adapter failures are structured
            return _AgentLoopOutcome(
                False,
                decision=decision,
                failure_code=FailureCode.UPSTREAM_ERROR,
                diagnostic=type(exc).__name__,
            )
        if not heartbeat_ok:
            return _AgentLoopOutcome(
                False,
                decision=decision,
                capability_result=result,
                failure_code=FailureCode.TIMEOUT,
                fenced=True,
            )
        if result is not None and not result.succeeded:
            return _AgentLoopOutcome(
                False,
                decision=decision,
                capability_result=result,
                failure_code=result.failure_code or FailureCode.UPSTREAM_ERROR,
            )
        return _AgentLoopOutcome(True, decision=decision, capability_result=result)

    def _persist_prepared_action(
        self, dispatch: _Dispatch, decision: ResearchDecisionOutcome
    ) -> tuple[ResearchStep, Run] | None:
        now = self.backend.clock.now()
        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(dispatch.attempt.run_id)
            attempt = uow.attempts.get(dispatch.attempt.attempt_id)
            step = uow.research_steps.get(dispatch.step.step_id)
            if run is None or attempt is None or step is None:
                return None
            acceptance = AttemptValidator(uow).validate(
                attempt,
                run=run,
                current_plan_version=run.current_plan_version or 1,
            )
            if (
                acceptance is not AttemptAcceptance.ACCEPTED
                or step.status is not ResearchStepStatus.DECIDING
                or step.attempt_id != attempt.attempt_id
            ):
                return None

            expected_run_version = run.state_version
            run = self._consume_usage(run, decision.usage)
            descriptor = None
            capability_reservation = Usage()
            loop_budget_denied = False
            if isinstance(decision.envelope.action, QueryAction):
                descriptor = self.agent_loop_executor.registry.find_kind(
                    decision.envelope.action.capability_kind
                )
                if descriptor is not None:
                    capability_reservation = Usage(cost_usd=descriptor.cost_usd)
                    if (
                        run.budget.max_cost_usd is not None
                        and run.budget.max_cost_usd - run.budget.cost_usd_used < descriptor.cost_usd
                    ):
                        run = run.terminate(
                            TerminationReason.BUDGET_EXCEEDED,
                            now,
                        )
                loop = uow.research_loops.get(dispatch.loop.loop_id)
                boundary = dispatch.seed_boundary
                if loop is not None and boundary is not None:
                    prospective_usage = loop.usage.add(decision.usage).add(capability_reservation)
                    loop_budget_denied = (
                        boundary.max_tokens is not None
                        and prospective_usage.tokens > boundary.max_tokens
                    ) or (
                        boundary.max_cost_usd is not None
                        and prospective_usage.cost_usd > boundary.max_cost_usd
                    )
            prepared = step.model_copy(
                update={
                    "status": ResearchStepStatus.PREPARED,
                    "action": decision.envelope,
                    "reasoning_usage": decision.usage,
                    "capability_reservation": capability_reservation,
                    "failure_code": (
                        FailureCode.BUDGET_EXCEEDED.value if loop_budget_denied else None
                    ),
                    "updated_at": now,
                }
            )
            uow.research_steps.save(prepared, expected_status=ResearchStepStatus.DECIDING)
            uow.dedup.remember(
                prepared.decision_request_id,
                decision.envelope.model_dump(mode="json"),
            )
            if isinstance(decision.envelope.action, QueryAction):
                capability_id = (
                    descriptor.capability_id
                    if descriptor is not None
                    else decision.envelope.action.capability_kind.value
                )
                operation_id = self._stable_id("operation", prepared.capability_request_id)
                if uow.operations.get(operation_id) is None:
                    uow.operations.save(
                        Operation(
                            operation_id=operation_id,
                            attempt_id=attempt.attempt_id,
                            capability_id=capability_id,
                            dedup_request_id=prepared.capability_request_id,
                            outcome_certainty=OutcomeCertainty.UNKNOWN,
                            started_at=now,
                        )
                    )
                uow.dedup.try_claim(
                    prepared.capability_request_id,
                    run_id=run.run_id,
                    kind="research_capability",
                )
            uow.events.append(
                [
                    self._event(
                        run,
                        EffectType.RESEARCH_STEP_PREPARED,
                        now,
                        task=dispatch.task,
                        attempt=attempt,
                        payload={
                            "loop_id": prepared.loop_id,
                            "step_id": prepared.step_id,
                            "action": decision.envelope.action.kind.value,
                        },
                    )
                ]
            )
            uow.runs.save(run, expected_version=expected_run_version)
            uow.commit()
            return prepared, run

    async def _with_lease_heartbeat(self, dispatch: _Dispatch, awaitable):
        """Await an external call and renew the same lease epoch every TTL/3."""

        operation = asyncio.create_task(awaitable)
        interval = max(0.01, self.lease_ttl_seconds / 3)
        while True:
            done, _pending = await asyncio.wait({operation}, timeout=interval)
            if done:
                return await operation, True
            try:
                with self.backend.unit_of_work() as uow:
                    lease = uow.leases.get(dispatch.task.task_id)
                    if (
                        lease is None
                        or lease.attempt_id != dispatch.attempt.attempt_id
                        or lease.epoch != dispatch.attempt.lease_epoch
                        or not lease.state.is_active
                    ):
                        raise RuntimeError("lease was replaced")
                    LeaseManager(
                        uow,
                        self.backend.clock,
                        self.backend.idgen,
                        lease_ttl_seconds=self.lease_ttl_seconds,
                    ).renew(lease)
                    uow.commit()
            except Exception:  # noqa: BLE001 - renewal failure fences the result
                return await operation, False

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}:{digest}"

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
            classifier = RetryClassifier(
                self.limits, base_backoff_seconds=self.base_backoff_seconds
            )
            checkpoints = CheckpointService(uow, clock, idgen)

            for dispatch, outcome in outcomes:
                task = dispatch.task
                attempt = dispatch.attempt
                fetched_attempt = uow.attempts.get(attempt.attempt_id)
                if fetched_attempt is None:
                    continue
                acceptance = validator.validate(
                    fetched_attempt,
                    run=run,
                    current_plan_version=run.current_plan_version or 1,
                )
                if dispatch.mode is ResearchExecutionMode.AGENT_LOOP and outcome.fenced:
                    acceptance = AttemptAcceptance.REJECTED_STALE_LEASE

                if (
                    dispatch.mode is ResearchExecutionMode.AGENT_LOOP
                    and acceptance is AttemptAcceptance.ACCEPTED
                ):
                    outcome: _AgentLoopOutcome
                    capability_usage = (
                        outcome.capability_result.usage
                        if outcome.capability_result is not None
                        else Usage()
                    )
                    run = self._consume_usage(run, capability_usage)
                    if outcome.succeeded:
                        self._accept_agent_success(
                            uow,
                            run,
                            dispatch,
                            fetched_attempt,
                            outcome,
                            now,
                            checkpoints,
                        )
                        accepted += 1
                    else:
                        disposition = self._accept_agent_failure(
                            uow,
                            run,
                            dispatch,
                            fetched_attempt,
                            outcome,
                            now,
                            classifier,
                            checkpoints,
                        )
                        if disposition == "retried":
                            retried += 1
                        else:
                            failed += 1
                elif acceptance is AttemptAcceptance.ACCEPTED:
                    outcome: TaskExecutionOutcome
                    # Fixed-fanout usage is consumed once at final TaskResult accept.
                    run = self._consume_usage(run, outcome.usage)
                    if outcome.succeeded:
                        self._accept_success(
                            uow, run, task, fetched_attempt, outcome, now, checkpoints
                        )
                        accepted += 1
                    else:
                        retried_or_failed = self._accept_failure(
                            uow,
                            run,
                            task,
                            fetched_attempt,
                            outcome,
                            now,
                            classifier,
                            checkpoints,
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

    def _accept_agent_success(
        self,
        uow,
        run: Run,
        dispatch: _Dispatch,
        attempt: Attempt,
        outcome: _AgentLoopOutcome,
        now,
        checkpoints,
    ) -> None:
        if (
            outcome.decision is None
            or dispatch.loop is None
            or dispatch.step is None
            or dispatch.seed_boundary is None
            or self.agent_loop_executor is None
        ):
            raise RuntimeError("incomplete agent-loop success outcome")
        step = uow.research_steps.get(dispatch.step.step_id)
        loop = uow.research_loops.get(dispatch.loop.loop_id)
        if (
            step is None
            or loop is None
            or step.status is not ResearchStepStatus.PREPARED
            or step.attempt_id != attempt.attempt_id
        ):
            raise RuntimeError("research step changed before accept")

        action = outcome.decision.envelope.action
        capability_usage = (
            outcome.capability_result.usage if outcome.capability_result is not None else Usage()
        )
        accumulated_capability_usage = step.capability_usage.add(capability_usage)
        evidence = (
            tuple(outcome.capability_result.evidence)
            if outcome.capability_result is not None
            else ()
        )
        if evidence:
            uow.evidence.save_many(run.run_id, attempt.attempt_id, evidence)

        event_effects = [EffectType.RESEARCH_ACTION_ACCEPTED]
        event_payload: dict[str, object] = {
            "loop_id": loop.loop_id,
            "step_id": step.step_id,
            "action": action.kind.value,
            "coverage": [cap.value for cap in loop.coverage],
            "reasoning_tokens": step.reasoning_usage.tokens,
            "capability_tokens": capability_usage.tokens,
        }
        coverage = list(loop.coverage)
        evidence_ids = list(loop.accepted_evidence_ids)
        direction_count = loop.direction_count
        close_status: ResearchLoopStatus | None = None
        degradation_reason: str | None = None

        if isinstance(action, QueryAction):
            if action.capability_kind not in coverage:
                coverage.append(action.capability_kind)
            for item in evidence:
                if item.evidence_id not in evidence_ids:
                    evidence_ids.append(item.evidence_id)
            event_effects.append(EffectType.RESEARCH_QUERY_ACCEPTED)
            if outcome.capability_result is not None:
                operation_id = self._stable_id("operation", step.capability_request_id)
                operation = uow.operations.get(operation_id)
                if operation is not None:
                    uow.operations.save(
                        operation.model_copy(
                            update={
                                "outcome_certainty": OutcomeCertainty.CONFIRMED,
                                "finished_at": now,
                            }
                        )
                    )
                uow.dedup.remember(
                    step.capability_request_id,
                    outcome.capability_result.model_dump(mode="json"),
                )
        elif isinstance(action, AddDirectionAction):
            prepared = self.agent_loop_executor.direction_policy.prepare(
                action.hint,
                approved_capabilities=tuple(dispatch.allowed_capabilities),
            )
            duplicate = uow.research_directions.by_focus_hash(loop.loop_id, prepared.focus_hash)
            if duplicate is None:
                direction = ResearchDirection(
                    direction_id=self._stable_id("direction", loop.loop_id, prepared.focus_hash),
                    loop_id=loop.loop_id,
                    run_id=loop.run_id,
                    plan_version=loop.plan_version,
                    task_id=loop.task_id,
                    parent_direction_id=action.parent_direction_id,
                    text=prepared.text,
                    focus_hash=prepared.focus_hash,
                    capability_scope=prepared.capability_scope,
                    source_step_id=step.step_id,
                    created_at=now,
                )
                uow.research_directions.save(direction)
                direction_count += 1
                event_effects.append(EffectType.RESEARCH_DIRECTION_ADDED)
                event_payload["direction_id"] = direction.direction_id
            else:
                event_effects.append(EffectType.RESEARCH_DIRECTION_DEDUPED)
                event_payload["direction_id"] = duplicate.direction_id
        elif isinstance(action, StopRequestAction):
            event_effects.append(EffectType.RESEARCH_STOP_REQUESTED)
            persisted_steps = uow.research_steps.by_loop(loop.loop_id)
            guard_result = LoopGuard().evaluate(
                loop,
                dispatch.seed_boundary,
                action,
                other_in_flight_steps=sum(
                    item.step_id != step.step_id
                    and item.status
                    in {
                        ResearchStepStatus.DECIDING,
                        ResearchStepStatus.PREPARED,
                    }
                    for item in persisted_steps
                ),
                accepted_step_count=sum(
                    item.status is ResearchStepStatus.ACCEPTED for item in persisted_steps
                ),
                persisted_direction_count=sum(
                    item.source_step_id is not None
                    for item in uow.research_directions.by_loop(loop.loop_id)
                ),
            )
            if guard_result.accepted:
                close_status = ResearchLoopStatus.COMPLETED
                event_effects.append(EffectType.RESEARCH_LOOP_COMPLETED)
            else:
                event_effects.append(EffectType.RESEARCH_STOP_REJECTED)
                event_payload["stop_rejection_reasons"] = list(guard_result.reasons)

        accepted_step = step.model_copy(
            update={
                "status": ResearchStepStatus.ACCEPTED,
                "action": outcome.decision.envelope,
                "capability_usage": accumulated_capability_usage,
                "accepted_evidence_ids": tuple(item.evidence_id for item in evidence),
                "result_operation_id": (
                    outcome.capability_result.operation_id
                    if outcome.capability_result is not None
                    else None
                ),
                "updated_at": now,
            }
        )
        uow.research_steps.save(accepted_step, expected_status=ResearchStepStatus.PREPARED)

        step_usage = step.reasoning_usage.add(accumulated_capability_usage)
        updated_loop = loop.model_copy(
            update={
                "next_step_index": loop.next_step_index + 1,
                "step_count": loop.step_count + 1,
                "direction_count": direction_count,
                "accepted_evidence_ids": tuple(evidence_ids),
                "coverage": tuple(coverage),
                "usage": loop.usage.add(step_usage),
                "state_version": loop.state_version + 1,
                "updated_at": now,
            }
        )
        if close_status is None and self._exhausted_after_accept(
            updated_loop, dispatch.seed_boundary
        ):
            close_status = ResearchLoopStatus.EXHAUSTED
            degradation_reason = self._loop_exhaustion_reason(updated_loop, dispatch.seed_boundary)
            event_effects.append(EffectType.RESEARCH_LOOP_EXHAUSTED)
            event_payload["reason"] = degradation_reason
        if close_status is not None:
            updated_loop = updated_loop.model_copy(
                update={
                    "status": close_status,
                    "degradation_reason": degradation_reason,
                }
            )
        uow.research_loops.save(updated_loop, expected_version=loop.state_version)

        uow.attempts.save(
            attempt.model_copy(
                update={
                    "state": AttemptState.SUCCEEDED,
                    "acceptance": AttemptAcceptance.ACCEPTED,
                    "finished_at": now,
                }
            )
        )
        self._release_accepted_lease(uow, attempt, now)
        current_task = uow.tasks.get(dispatch.task.task_id)
        target = TaskState.SUCCEEDED if updated_loop.status.is_closed else TaskState.PENDING
        task_changes = (
            {"accepted_attempt_id": attempt.attempt_id} if target is TaskState.SUCCEEDED else {}
        )
        uow.tasks.save(current_task.transition(target, now, **task_changes))

        event_payload["coverage"] = [cap.value for cap in updated_loop.coverage]
        event_payload["evidence_ids"] = list(accepted_step.accepted_evidence_ids)
        uow.events.append(
            [
                self._event(
                    run,
                    effect,
                    now,
                    task=dispatch.task,
                    attempt=attempt,
                    payload=event_payload,
                )
                for effect in dict.fromkeys(event_effects)
            ]
        )
        checkpoints.record(
            run_id=run.run_id,
            kind=CheckpointKind.RESEARCH_STEP,
            state_version=run.state_version,
            plan_version=run.current_plan_version,
            reason=f"accepted research step {step.step_id}",
        )

    def _accept_agent_failure(
        self,
        uow,
        run: Run,
        dispatch: _Dispatch,
        attempt: Attempt,
        outcome: _AgentLoopOutcome,
        now,
        classifier,
        checkpoints,
    ) -> str:
        if dispatch.loop is None or dispatch.step is None:
            raise RuntimeError("incomplete agent-loop failure outcome")
        step = uow.research_steps.get(dispatch.step.step_id)
        loop = uow.research_loops.get(dispatch.loop.loop_id)
        if step is None or loop is None or step.attempt_id != attempt.attempt_id:
            raise RuntimeError("research step changed before failure accept")
        code = outcome.failure_code or FailureCode.UNKNOWN
        retry_count = step.retry_count + 1
        capability_usage = (
            outcome.capability_result.usage if outcome.capability_result is not None else Usage()
        )
        accumulated_capability_usage = step.capability_usage.add(capability_usage)
        failed_step = step.model_copy(
            update={
                "status": ResearchStepStatus.FAILED,
                "retry_count": retry_count,
                "capability_usage": accumulated_capability_usage,
                "failure_code": code.value,
                "updated_at": now,
            }
        )
        uow.research_steps.save(failed_step, expected_status=step.status)
        uow.attempts.save(attempt.fail(code, now, acceptance=AttemptAcceptance.ACCEPTED))
        self._release_accepted_lease(uow, attempt, now)

        decision = classifier.classify(
            failure_code=code,
            attempts_used=retry_count,
            max_attempts=run.policy.max_attempts_per_task,
        )
        retry = decision.retry or (
            code is FailureCode.INVALID_RESPONSE and retry_count < run.policy.max_attempts_per_task
        )
        current_task = uow.tasks.get(dispatch.task.task_id)
        if retry:
            uow.tasks.save(current_task.transition(TaskState.AWAITING_RETRY, now))
            checkpoints.record(
                run_id=run.run_id,
                kind=CheckpointKind.RETRY,
                state_version=run.state_version,
                plan_version=run.current_plan_version,
                reason=(
                    f"retry research step {step.step_id} after {code.value}; "
                    f"step_retry={retry_count}"
                ),
            )
            disposition = "retried"
        else:
            # Step-local exhaustion degrades this seed loop; global budget/deadline
            # termination is handled on the Run and never represented as STOP.
            final_usage = step.reasoning_usage.add(accumulated_capability_usage)
            degradation_reason = (
                "loop_budget_exhausted"
                if code is FailureCode.BUDGET_EXCEEDED and not run.is_terminal
                else f"step_failure:{code.value}"
            )
            exhausted = loop.model_copy(
                update={
                    "status": ResearchLoopStatus.EXHAUSTED,
                    "usage": loop.usage.add(final_usage),
                    "degradation_reason": degradation_reason,
                    "state_version": loop.state_version + 1,
                    "updated_at": now,
                }
            )
            uow.research_loops.save(exhausted, expected_version=loop.state_version)
            uow.tasks.save(
                current_task.transition(
                    TaskState.SUCCEEDED, now, accepted_attempt_id=attempt.attempt_id
                )
            )
            disposition = "failed"

        effects = [EffectType.RESEARCH_ACTION_REJECTED]
        if not retry:
            effects.append(EffectType.RESEARCH_LOOP_EXHAUSTED)
        payload = {
            "loop_id": loop.loop_id,
            "step_id": step.step_id,
            "failure_code": code.value,
            "step_retry_count": retry_count,
            "diagnostic": outcome.diagnostic,
        }
        uow.events.append(
            [
                self._event(
                    run,
                    effect,
                    now,
                    task=dispatch.task,
                    attempt=attempt,
                    payload=payload,
                )
                for effect in effects
            ]
        )
        return disposition

    @staticmethod
    def _exhausted_after_accept(loop: ResearchLoop, boundary: SeedExplorationBoundary) -> bool:
        if loop.step_count >= boundary.max_steps:
            return True
        if boundary.max_tokens is not None and loop.usage.tokens >= boundary.max_tokens:
            return True
        if boundary.max_cost_usd is not None and loop.usage.cost_usd >= boundary.max_cost_usd:
            return True
        return False

    def _accept_success(self, uow, run, task, attempt, outcome, now, checkpoints) -> None:
        result = outcome.task_result
        result_ref = result.artifacts[0] if result and result.artifacts else None
        for artifact in result.artifacts if result else ():
            uow.artifacts.record_metadata(artifact)
        if result and result.evidence:
            uow.evidence.save_many(run.run_id, attempt.attempt_id, result.evidence)
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
        self._release_accepted_lease(uow, attempt, now)
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
        self._release_accepted_lease(uow, attempt, now)
        current = uow.tasks.get(task.task_id)
        if decision.retry:
            uow.tasks.save(current.transition(TaskState.AWAITING_RETRY, now))
            checkpoints.record(
                run_id=run.run_id,
                kind=CheckpointKind.RETRY,
                state_version=run.state_version,
                plan_version=run.current_plan_version,
                reason=(
                    f"retry {task.task_id} after {code.value} backoff={decision.backoff_seconds}s"
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

    @staticmethod
    def _release_accepted_lease(uow, attempt, now) -> None:
        lease = uow.leases.get(attempt.task_id)
        if (
            lease is not None
            and lease.attempt_id == attempt.attempt_id
            and lease.epoch == attempt.lease_epoch
            and lease.state.is_active
        ):
            uow.leases.save(lease.release(now), expected_epoch=lease.epoch)

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

    def _readmit_retry_ready(self, uow, run, now) -> None:
        """Re-queue retryable tasks whose backoff has elapsed (AWAITING_RETRY → READY).

        backoff is recomputed from ``attempt_count`` (same formula as
        :class:`RetryClassifier`), starting at ``task.updated_at`` (the moment
        the task entered AWAITING_RETRY). No real timer — deterministic on the
        injected ``clock``.
        """

        for task in uow.tasks.by_run(run.run_id):
            if task.state is not TaskState.AWAITING_RETRY:
                continue
            attempts_used = task.attempt_count
            plan = (
                uow.plans.get(run.run_id, task.plan_version)
                if task.plan_version == (run.current_plan_version or task.plan_version)
                else None
            )
            if (
                plan is not None
                and plan.graph.research_execution_mode is ResearchExecutionMode.AGENT_LOOP
            ):
                loop = uow.research_loops.for_task(run.run_id, task.plan_version, task.task_id)
                step = (
                    uow.research_steps.by_logical_key(
                        run.run_id,
                        task.plan_version,
                        task.task_id,
                        loop.next_step_index,
                    )
                    if loop is not None
                    else None
                )
                if step is not None:
                    attempts_used = step.retry_count
            backoff = retry_backoff_seconds(max(1, attempts_used), base=self.base_backoff_seconds)
            if now >= task.updated_at + timedelta(seconds=backoff):
                uow.tasks.save(task.transition(TaskState.READY, now))

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
        payload: dict | None = None,
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
            payload=payload or {},
        )

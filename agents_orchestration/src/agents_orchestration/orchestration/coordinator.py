"""RunCoordinator: bounded Run-level lifecycle dispatcher (Ch.4 tasks 4.1-4.10).

Sits above the Task Runtime (``RuntimeTick``). ``advance(run_id)`` executes at
most one bounded semantic step, routing to a phase handler selected solely from
durable Run state, and returns a structured :class:`AdvanceReport`.

Each phase follows the prepare/execute/accept protocol (design Decision 4):
the coordinator captures input versions in a short read, the handler's async
``execute`` invokes providers *outside* any write transaction, then the
handler's ``accept`` performs phase-specific persistence (GoalSpec/Contract,
Plan/Tasks, …) inside the write transaction while the coordinator records the
stage record, Event, and semantic Checkpoint atomically.

Recovery rules (task 11.1): the coordinator is state-driven — a process restart
reloads durable Run/Task/Stage state and resumes from ``phase_for_state``.
Per phase: CREATED re-initializes; Goal/Plan re-read persisted GoalSpec/Contract
and Plan; Research/Analyze/Write/Review re-drive eligible Tasks (the Task
Runtime keeps Attempt/Lease/retry); Finalize re-evaluates Completion. Per
StageExecution status: ACCEPTED results are reused (idempotent prepare, one per
run+logical_stage+fingerprint) so a provider is never re-invoked for already-
accepted work; PREPARED records with an unknown external outcome are re-executed
on the next advance (never assumed successful); REJECTED/FAILED/SUPERSEDED are
retained as observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from agents_orchestration.domain.artifact import ArtifactRef
from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    AdvanceReport,
    CapturedVersions,
    InputFingerprint,
    PhaseId,
    PhaseResultClassification,
    StageExecution,
    StageStatus,
    TaskTickSummary,
    build_gate_continuation,
    classify_phase_result,
    phase_for_state,
    stage_idempotency_key,
    stage_logical_key,
)
from agents_orchestration.domain.enums import (
    EffectType,
    FailureCode,
    GateType,
    RunState,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.lifecycle import (
    Checkpoint,
    CheckpointKind,
    GateContinuationIntent,
)
from agents_orchestration.domain.state_machine import assert_run_transition

if TYPE_CHECKING:
    from agents_orchestration.runtime.persistence.connection import SqliteBackend


@dataclass(frozen=True)
class PhaseContext:
    """Read-only snapshot captured before the handler runs (design Decision 4)."""

    run: Run
    phase: PhaseId
    captured: CapturedVersions


@dataclass(frozen=True)
class PhaseOutcome:
    """What a phase handler produced for one advance.

    ``disposition`` is PROGRESSED / BLOCKED / IDLE — never TERMINAL; the
    coordinator owns terminal transitions through deterministic policy and
    guards. ``proposal`` carries the phase-specific payload (e.g. a
    GoalNormalizationOutcome or PlanProposal) that ``accept`` consumes.
    """

    disposition: AdvanceDisposition
    next_state: RunState | None = None
    reason: str = ""
    stage_logical_key: str = ""
    input_fingerprint: InputFingerprint | None = None
    output_refs: tuple[ArtifactRef, ...] = ()
    output_entities: tuple[str, ...] = ()
    task_tick: TaskTickSummary | None = None
    open_gate: GateType | None = None
    gate_intent: GateContinuationIntent | None = None
    gate_feedback: str | None = None
    gate_correlation_id: str | None = None
    gate_artifact_hash: str | None = None
    gate_context: dict[str, object] | None = None
    failure_code: FailureCode | None = None
    proposal: object | None = None
    bump_revision: bool = False
    bump_replan: bool = False
    counts_toward_idle_budget: bool = True
    continue_immediately: bool = True
    # analyze-sufficiency-feedback 5.4/6.1: explicit phase signals consumed by
    # the coordinator's deterministic accept branches — never inferred from
    # ``reason`` text.
    handled_accept: bool = False
    termination_reason: TerminationReason | None = None


class PhaseHandler(Protocol):
    """One phase of the coordinated Run lifecycle (Ch.5-7 implement these)."""

    phase: PhaseId

    async def execute(self, ctx: PhaseContext, backend: SqliteBackend) -> PhaseOutcome:
        """Invoke providers OUTSIDE any write transaction and return an outcome."""
        ...

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now: datetime) -> Run:
        """Persist phase-specific accepted outputs inside the write transaction
        and return the resulting Run (possibly transitioned). Called only for
        PROGRESSED outcomes on non-stale runs (task 4.5)."""
        ...


@dataclass(frozen=True)
class CoordinatorDiagnostics:
    """Structured, secret-redacted diagnostics for coordinator errors (task 4.10)."""

    code: str
    message: str
    run_id: str
    phase: PhaseId | None = None
    stage_logical_key: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "run_id": self.run_id,
            "phase": self.phase.value if self.phase else None,
            "stage_logical_key": self.stage_logical_key,
        }


class RunCoordinator:
    """Advance a Run by at most one bounded semantic step per call (task 4.2)."""

    def __init__(
        self,
        backend: SqliteBackend,
        phase_handlers: dict[PhaseId, PhaseHandler],
    ) -> None:
        self.backend = backend
        self.handlers = dict(phase_handlers)

    async def advance(self, run_id: str) -> AdvanceReport:
        run = self._load(run_id)

        if run.is_terminal:  # task 4.3
            return self._report(
                run, run.state, AdvanceDisposition.TERMINAL, reason=self._terminal_reason(run)
            )

        if run.state is RunState.CREATED:  # task 4.1: CREATED -> NORMALIZING
            return self._advance_created(run)

        self._reconcile_legacy_tasks(run_id)  # remove-noop-phase-tasks 5.x

        phase = phase_for_state(run.state)
        if phase is None:  # task 4.3: paused / gate-waiting have no active phase
            return self._report(
                run, run.state, AdvanceDisposition.IDLE, reason=f"non-active:{run.state.value}"
            )

        if self._has_open_gate(run_id):  # task 4.3: open Gate blocks the phase
            return self._report(run, run.state, AdvanceDisposition.BLOCKED, reason="open-gate")

        guard = self._guard_terminate(run)  # task 4.9
        if guard is not None:
            return guard

        handler = self.handlers.get(phase)
        if handler is None:
            return self._report(
                run, run.state, AdvanceDisposition.IDLE, reason=f"no-handler:{phase.value}"
            )

        captured = CapturedVersions(
            state_version=run.state_version,
            plan_version=run.current_plan_version,
        )
        ctx = PhaseContext(run=run, phase=phase, captured=captured)
        outcome = await handler.execute(ctx, self.backend)  # outside write txn (4.5)
        return self._accept(run.run_id, phase, outcome, captured)  # one phase (4.4)

    # --- short-circuits ------------------------------------------------------

    def _advance_created(self, run: Run) -> AdvanceReport:
        now = self.backend.clock.now()
        moved = run.transition(RunState.NORMALIZING, now)
        with self.backend.unit_of_work() as uow:
            uow.runs.save(moved, expected_version=run.state_version)
            uow.events.append([self._event(moved, EffectType.RUN_STATE_TRANSITION, now)])
            uow.checkpoints.save(
                self._checkpoint(moved, CheckpointKind.PLAN, "created->normalizing", now)
            )
            uow.commit()
        return AdvanceReport(
            run_id=run.run_id,
            from_state=run.state,
            to_state=moved.state,
            disposition=AdvanceDisposition.PROGRESSED,
            reason="created->normalizing",
            state_version=moved.state_version,
        )

    def _guard_terminate(self, run: Run) -> AdvanceReport | None:
        now = self.backend.clock.now()
        if run.budget.exhausted(now):
            reason = (
                TerminationReason.DEADLINE_EXCEEDED
                if run.budget.deadline_exceeded(now)
                else TerminationReason.BUDGET_EXCEEDED
            )
            return self._terminate(run, reason)
        return None

    def _terminate(self, run: Run, reason: TerminationReason) -> AdvanceReport:
        now = self.backend.clock.now()
        moved = run.terminate(reason, now)
        with self.backend.unit_of_work() as uow:
            uow.runs.save(moved, expected_version=run.state_version)
            uow.events.append(
                [self._event(moved, EffectType.RUN_TERMINATED, now, kind=reason.value)]
            )
            uow.checkpoints.save(
                self._checkpoint(
                    moved, CheckpointKind.FINALIZATION, f"terminate:{reason.value}", now
                )
            )
            uow.commit()
        return self._report(moved, moved.state, AdvanceDisposition.TERMINAL, reason=reason.value)

    # --- outcome acceptance --------------------------------------------------

    def _accept(self, run_id, phase, outcome, captured):
        current = self._load(run_id)
        now = self.backend.clock.now()
        current_versions = CapturedVersions(
            state_version=current.state_version, plan_version=current.current_plan_version
        )
        stale = classify_phase_result(captured, current_versions) is PhaseResultClassification.STALE
        if stale:  # task 4.6: stale result becomes an observation, no advance
            return self._accept_observation(
                current,
                phase,
                outcome,
                now,
                reason=f"{phase.value}:stale-observation",
                counts_toward_idle_budget=False,
            )
        if outcome.disposition is AdvanceDisposition.BLOCKED:
            return self._accept_blocked(current, phase, outcome, now)
        if outcome.disposition is AdvanceDisposition.IDLE:
            return self._accept_observation(
                current,
                phase,
                outcome,
                now,
                reason=outcome.reason or f"{phase.value}:idle",
                counts_toward_idle_budget=outcome.counts_toward_idle_budget,
            )
        # PROGRESSED: handler.accept does phase-specific persistence.
        if outcome.termination_reason is not None:
            # 6.1/6.2: explicit deterministic termination (e.g. gap budget
            # exhausted) — terminate atomically and return TERMINAL, never a
            # plain IDLE that would re-invoke the provider.
            return self._accept_terminal(current, phase, outcome, now)
        with self.backend.unit_of_work() as uow:
            new_run = self.handlers[phase].accept(outcome, current, uow, now)
            if outcome.handled_accept:
                # The handler performed the full atomic accept itself (e.g. the
                # ANALYZE focused replan already transitioned the Run and emitted
                # PLAN_REPLANNED + RUN_STATE_TRANSITION).
                pass
            else:
                if new_run.state is not current.state:
                    uow.events.append(
                        [
                            self._event(
                                new_run,
                                EffectType.RUN_STATE_TRANSITION,
                                now,
                                payload={"phase": phase.value},
                            )
                        ]
                    )
            # A handler-owned transition does not imply handler-owned Stage
            # persistence. Every accepted phase result keeps its structured
            # observation under the captured pre-transition fingerprint.
            if outcome.input_fingerprint is not None:
                self._persist_accepted_stage(uow, current, phase, outcome, now)
            uow.checkpoints.save(
                self._checkpoint(
                    new_run,
                    self._checkpoint_kind(phase),
                    outcome.reason or f"{phase.value}:{outcome.disposition.value}",
                    now,
                )
            )
            uow.commit()
        return AdvanceReport(
            run_id=current.run_id,
            from_state=current.state,
            to_state=new_run.state,
            disposition=outcome.disposition,
            reason=outcome.reason,
            state_version=new_run.state_version,
            stage_logical_key=outcome.stage_logical_key or None,
            task_tick=outcome.task_tick,
            continue_immediately=outcome.continue_immediately,
        )

    def _accept_terminal(self, run, phase, outcome, now):
        """Atomic deterministic termination from a phase outcome (task 6.2).

        Records the observing Stage, the terminal Run (FAILED + reason), the
        RUN_TERMINATED event and a checkpoint in one transaction, then returns
        TERMINAL. Used for ANALYZE research-gap budget exhaustion
        (REQUIRED_EVIDENCE_MISSING)."""

        reason = outcome.termination_reason
        moved = run.terminate(reason, now)
        with self.backend.unit_of_work() as uow:
            uow.runs.save(moved, expected_version=run.state_version)
            if outcome.input_fingerprint is not None:
                self._persist_observation_stage(uow, run, phase, outcome, now)
            uow.events.append(
                [self._event(moved, EffectType.RUN_TERMINATED, now, kind=reason.value)]
            )
            uow.checkpoints.save(
                self._checkpoint(
                    moved, CheckpointKind.FINALIZATION, f"terminate:{reason.value}", now
                )
            )
            uow.commit()
        return self._report(moved, moved.state, AdvanceDisposition.TERMINAL, reason=reason.value)

    def _accept_blocked(self, run, phase, outcome, now):
        with self.backend.unit_of_work() as uow:
            if outcome.input_fingerprint is not None:
                self._persist_observation_stage(uow, run, phase, outcome, now)
            if outcome.open_gate is GateType.PLAN_APPROVAL:
                self.handlers[phase].persist_for_approval(outcome, run, uow, now)
            if outcome.open_gate is not None:
                self._open_gate(uow, run, outcome)  # emits GATE_OPENED
            # No RUN_STATE_TRANSITION event: a BLOCKED outcome does not change
            # Run state (from_state == to_state); the gate/observation record
            # carries the blocking condition.
            uow.commit()
        return AdvanceReport(
            run_id=run.run_id,
            from_state=run.state,
            to_state=run.state,
            disposition=AdvanceDisposition.BLOCKED,
            reason=outcome.reason or f"{phase.value}:blocked",
            state_version=run.state_version,
            stage_logical_key=outcome.stage_logical_key or None,
            task_tick=outcome.task_tick,
            continue_immediately=False,
        )

    def _accept_observation(
        self,
        run,
        phase,
        outcome,
        now,
        *,
        reason,
        counts_toward_idle_budget,
    ):
        exhausted = False
        with self.backend.unit_of_work() as uow:
            fingerprint = outcome.input_fingerprint or InputFingerprint(
                state_version=run.state_version,
                plan_version=run.current_plan_version,
            )
            uow.stages.save(
                self._build_stage(
                    run,
                    phase,
                    outcome,
                    StageStatus.PREPARED,
                    now,
                    fingerprint=fingerprint,
                    counts_toward_idle_budget=counts_toward_idle_budget,
                )
            )
            # Count only the newest consecutive, budget-consuming IDLE
            # observations for the current fingerprint. Historical plans,
            # BLOCKED/stale results and retry WAITING records break the streak.
            logical = outcome.stage_logical_key or stage_logical_key(phase)
            fingerprint_hex = fingerprint.hexdigest()
            consecutive = 0
            for stage in reversed(uow.stages.for_logical_stage(run.run_id, logical)):
                if (
                    stage.status is not StageStatus.PREPARED
                    or stage.fingerprint.hexdigest() != fingerprint_hex
                    or not stage.counts_toward_idle_budget
                ):
                    break
                consecutive += 1
            exhausted = consecutive >= run.policy.max_attempts_per_task
            uow.commit()
        if exhausted:
            return self._terminate(run, TerminationReason.ATTEMPTS_EXHAUSTED)
        return AdvanceReport(
            run_id=run.run_id,
            from_state=run.state,
            to_state=run.state,
            disposition=AdvanceDisposition.IDLE,
            reason=reason,
            state_version=run.state_version,
            stage_logical_key=outcome.stage_logical_key or None,
            task_tick=outcome.task_tick,
            continue_immediately=outcome.continue_immediately,
        )

    def _open_gate(self, uow, run, outcome) -> None:
        from agents_orchestration.orchestration.gates import GateService

        cont = build_gate_continuation(
            outcome.open_gate,
            run,
            intent=outcome.gate_intent,
            feedback=outcome.gate_feedback,
            correlation_id=outcome.gate_correlation_id,
            artifact_hash=outcome.gate_artifact_hash,
        )
        GateService(uow, self.backend.clock, self.backend.idgen).open(
            run,
            outcome.open_gate,
            actor="system",
            role="orchestrator",
            scope=run.run_id,
            artifact_hash=outcome.gate_artifact_hash,
            context=outcome.gate_context,
            continuation=cont,
        )

    # --- stage / event / checkpoint helpers ----------------------------------

    def _persist_accepted_stage(self, uow, run, phase, outcome, now) -> None:
        stage = self._build_stage(run, phase, outcome, StageStatus.PREPARED, now)
        prepared = uow.stages.prepare(stage)
        if prepared.status is StageStatus.ACCEPTED:
            return
        # Candidate artifacts are filesystem-only until this accepted-stage
        # transaction makes them authoritative. A stale/CAS-losing candidate
        # therefore remains a reclaimable orphan with no metadata visibility.
        for ref in outcome.output_refs:
            uow.artifacts.record_metadata(ref)
        accepted = prepared.transition(
            StageStatus.ACCEPTED,
            at=now,
            output_artifact_refs=tuple(outcome.output_refs),
            output_entity_ids=tuple(outcome.output_entities),
            failure_code=None,
            counts_toward_idle_budget=False,
        )
        uow.stages.accept(prepared.stage_execution_id, accepted=accepted)

    def _persist_observation_stage(self, uow, run, phase, outcome, now) -> None:
        uow.stages.save(self._build_stage(run, phase, outcome, StageStatus.PREPARED, now))

    def _build_stage(
        self,
        run,
        phase,
        outcome,
        status,
        now,
        *,
        fingerprint=None,
        counts_toward_idle_budget=False,
    ) -> StageExecution:
        fp = fingerprint or outcome.input_fingerprint
        logical = outcome.stage_logical_key or stage_logical_key(phase)
        return StageExecution(
            stage_execution_id=self.backend.idgen.new_id("stage"),
            run_id=run.run_id,
            phase=phase,
            logical_stage_key=logical,
            fingerprint=fp,
            status=status,
            output_artifact_refs=tuple(outcome.output_refs),
            output_entity_ids=tuple(outcome.output_entities),
            failure_code=outcome.failure_code,
            counts_toward_idle_budget=counts_toward_idle_budget,
            idempotency_key=stage_idempotency_key(run.run_id, logical, fp.hexdigest()),
            created_at=now,
            updated_at=now,
        )

    def _event(self, run, effect, now, *, kind="", payload=None) -> DomainEvent:
        data = dict(payload or {})
        if kind:
            data["kind"] = kind
        return DomainEvent(
            event_id=self.backend.idgen.new_id("evt"),
            run_id=run.run_id,
            effect=effect,
            state_version=run.state_version,
            occurred_at=now,
            payload=data,
        )

    def _checkpoint(self, run, kind, reason, now) -> Checkpoint:
        return Checkpoint(
            checkpoint_id=self.backend.idgen.new_id("ckpt"),
            run_id=run.run_id,
            kind=kind,
            state_version=run.state_version,
            plan_version=run.current_plan_version,
            reason=reason,
            created_at=now,
        )

    @staticmethod
    def _checkpoint_kind(phase: PhaseId) -> CheckpointKind:
        return {
            PhaseId.GOAL: CheckpointKind.PLAN,
            PhaseId.PLAN: CheckpointKind.PLAN,
            PhaseId.RESEARCH: CheckpointKind.BRANCH_RESULT,
            PhaseId.REVIEW: CheckpointKind.REPLAN,
            PhaseId.FINALIZE: CheckpointKind.FINALIZATION,
        }.get(phase, CheckpointKind.RETRY)

    # --- legacy Plan reconciliation (remove-noop-phase-tasks 5.x) -----------

    def _reconcile_legacy_tasks(self, run_id: str) -> None:
        """Retire non-research Tasks carried by a legacy (pre-upgrade) Plan,
        without dispatching them. PENDING/READY → SKIPPED;
        DISPATCHED/AWAITING_RETRY → CANCELED with active leases invalidated so
        late results cannot advance the Run. Terminal history is preserved.
        Idempotent — terminal Tasks are never touched."""

        now = self.backend.clock.now()
        idgen = self.backend.idgen
        events: list[DomainEvent] = []
        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None or run.current_plan_version is None:
                uow.commit()
                return
            for task in uow.tasks.by_run(run_id, plan_version=run.current_plan_version):
                if task.worker_role is WorkerRole.EVIDENCE_RESEARCHER:
                    continue
                if task.state in (TaskState.PENDING, TaskState.READY):
                    uow.tasks.save(task.transition(TaskState.SKIPPED, now))
                    events.append(self._legacy_event(run, task, "skipped", now, idgen))
                elif task.state in (TaskState.DISPATCHED, TaskState.AWAITING_RETRY):
                    lease = uow.leases.get(task.task_id)
                    if lease is not None and lease.state.is_active:
                        uow.leases.save(lease.expire(), expected_epoch=lease.epoch)
                    uow.tasks.save(task.transition(TaskState.CANCELED, now))
                    events.append(self._legacy_event(run, task, "canceled", now, idgen))
            if events:
                uow.events.append(events)
            uow.commit()

    @staticmethod
    def _legacy_event(run, task, action, now, idgen) -> DomainEvent:
        return DomainEvent(
            event_id=idgen.new_id("evt"),
            run_id=run.run_id,
            effect=EffectType.TASK_STATE_TRANSITION,
            state_version=run.state_version,
            occurred_at=now,
            task_id=task.task_id,
            plan_version=run.current_plan_version,
            payload={"legacy_reconcile": action, "role": task.worker_role.value},
        )

    # --- read helpers --------------------------------------------------------

    def _load(self, run_id: str) -> Run:
        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _has_open_gate(self, run_id: str) -> bool:
        with self.backend.unit_of_work() as uow:
            return any(g.is_open for g in uow.gates.open_for_run(run_id))

    @staticmethod
    def _terminal_reason(run: Run) -> str:
        return f"terminal:{run.termination.value}" if run.termination else "terminal"

    @staticmethod
    def _report(run, to_state, disposition, *, reason, from_state=None):
        return AdvanceReport(
            run_id=run.run_id,
            from_state=from_state or run.state,
            to_state=to_state,
            disposition=disposition,
            reason=reason,
            state_version=run.state_version,
        )


def transition_or_stay(run: Run, target: RunState | None, now: datetime) -> Run:
    """Helper for simple phase ``accept`` impls: transition if ``target`` differs."""

    if target is not None and target is not run.state:
        assert_run_transition(run.state, target)
        return run.transition(target, now)
    return run


__all__ = [
    "CoordinatorDiagnostics",
    "PhaseContext",
    "PhaseHandler",
    "PhaseOutcome",
    "RunCoordinator",
    "transition_or_stay",
]

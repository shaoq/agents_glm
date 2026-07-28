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
    TerminationReason,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.lifecycle import Checkpoint, CheckpointKind
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
    failure_code: FailureCode | None = None
    proposal: object | None = None
    bump_revision: bool = False
    bump_replan: bool = False


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
                current, phase, outcome, now, reason=f"{phase.value}:stale-observation"
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
            )
        # PROGRESSED: handler.accept does phase-specific persistence.
        with self.backend.unit_of_work() as uow:
            new_run = self.handlers[phase].accept(outcome, current, uow, now)
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
        )

    def _accept_blocked(self, run, phase, outcome, now):
        with self.backend.unit_of_work() as uow:
            if outcome.input_fingerprint is not None:
                self._persist_observation_stage(uow, run, phase, outcome, now)
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
        )

    def _accept_observation(self, run, phase, outcome, now, *, reason):
        with self.backend.unit_of_work() as uow:
            if outcome.input_fingerprint is not None:
                self._persist_observation_stage(uow, run, phase, outcome, now)
            uow.commit()
        return AdvanceReport(
            run_id=run.run_id,
            from_state=run.state,
            to_state=run.state,
            disposition=AdvanceDisposition.IDLE,
            reason=reason,
            state_version=run.state_version,
            task_tick=outcome.task_tick,
        )

    def _open_gate(self, uow, run, outcome) -> None:
        from agents_orchestration.orchestration.gates import GateService

        cont = build_gate_continuation(outcome.open_gate, run)
        GateService(uow, self.backend.clock, self.backend.idgen).open(
            run,
            outcome.open_gate,
            actor="system",
            role="orchestrator",
            scope=run.run_id,
            allowed_response_schema="{}",
            continuation=cont,
        )

    # --- stage / event / checkpoint helpers ----------------------------------

    def _persist_accepted_stage(self, uow, run, phase, outcome, now) -> None:
        stage = self._build_stage(run, phase, outcome, StageStatus.PREPARED, now)
        uow.stages.prepare(stage)
        accepted = stage.transition(
            StageStatus.ACCEPTED,
            at=now,
            output_artifact_refs=tuple(outcome.output_refs),
            output_entity_ids=tuple(outcome.output_entities),
        )
        uow.stages.accept(stage.stage_execution_id, accepted=accepted)

    def _persist_observation_stage(self, uow, run, phase, outcome, now) -> None:
        uow.stages.save(self._build_stage(run, phase, outcome, StageStatus.PREPARED, now))

    def _build_stage(self, run, phase, outcome, status, now) -> StageExecution:
        fp = outcome.input_fingerprint
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

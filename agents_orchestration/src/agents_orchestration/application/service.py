"""Application use-case service and composition root (tasks 11.1 / 11.2).

The CLI is a thin adapter over :class:`OrchestrationService`; all domain logic
lives here and below. ``start_run`` is idempotent by Request ID and reports stale
Expected Version conflicts (task 11.2). The service composes the durable runtime,
planning, gates, capabilities and report layers built in Sections 3-10.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_orchestration.adapters.health import capability_doctor
from agents_orchestration.capabilities.registry import CapabilityRegistry
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.artifact import ArtifactRef
from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    AdvanceReport,
    ContinuationOutcome,
    resolve_gate_continuation,
)
from agents_orchestration.domain.enums import (
    EffectType,
    GateType,
    RunState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.lifecycle import GateContinuationIntent
from agents_orchestration.domain.plan import PlanAcceptance
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.orchestration.focused_replan import FocusedReplanBuilder
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidation, PlanValidator
from agents_orchestration.orchestration.proposals import PlanProposal
from agents_orchestration.orchestration.replan import ReplanService
from agents_orchestration.runtime.ports import OrphanArtifactError, StaleVersionError
from agents_orchestration.runtime.tick import RuntimeTick
from agents_orchestration.runtime.watch import RuntimeWatch
from agents_orchestration.workers.executor import WorkerExecutor
from agents_orchestration.workers.registry import WorkerRegistry


class DuplicateStartError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportedArtifact:
    artifact_id: str
    content_hash: str
    path: str
    size_bytes: int
    data: bytes


@dataclass(frozen=True)
class StartRunCommand:
    """Typed input for the unified create-and-drive entry (task 10.2)."""

    raw_goal: str
    request_id: str


class DefaultWorkerHandler:
    """Invoke the task's first registered capability and wrap it in a TaskResult.

    Real per-role prompt logic is layered on later; this keeps a default run
    executable end to end when a capability registry is supplied.
    """

    def __init__(self, registry: CapabilityRegistry, idgen) -> None:
        self.registry = registry
        self.idgen = idgen

    async def handle(self, task: Task, attempt: Attempt, run: Run, invoke) -> TaskResult:
        descriptor = next(
            (
                self.registry.find_kind(c)
                for c in task.required_capabilities
                if self.registry.find_kind(c)
            ),
            None,
        )
        if descriptor is None:
            return TaskResult(
                attempt_id=attempt.attempt_id,
                task_id=task.task_id,
                run_id=run.run_id,
                worker_role=task.worker_role,
                summary="no capability",
            )
        request = CapabilityRequest(
            request_id=self.idgen.new_id("creq"),
            capability_id=descriptor.capability_id,
            worker_id=f"worker::{task.worker_role.value}",
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            inputs={"query": run.raw_goal},
        )
        result = await invoke(request)
        return TaskResult(
            attempt_id=attempt.attempt_id,
            task_id=task.task_id,
            run_id=run.run_id,
            worker_role=task.worker_role,
            evidence=result.evidence,
            usage=result.usage,
            summary=task.worker_role.value,
        )


class OrchestrationService:
    def __init__(
        self,
        backend,
        *,
        limits: SystemLimits | None = None,
        capability_registry: CapabilityRegistry | None = None,
        run_policy: RunPolicy | None = None,
        coordinator=None,
    ) -> None:
        self.backend = backend
        self.limits = limits or SystemLimits()
        self.capability_registry = capability_registry or CapabilityRegistry()
        self.run_policy = run_policy or RunPolicy.from_limits(self.limits)
        self.workers = WorkerRegistry.default()
        self.router = CapabilityRouter(self.capability_registry, backend.idgen)
        handler = DefaultWorkerHandler(self.capability_registry, backend.idgen)
        self._handlers = {role: handler for role in WorkerRole}
        self._executor = WorkerExecutor(self.workers, self.router, self._handlers, self.run_policy)
        self._coordinator = coordinator

    # --- 11.1 / 11.2 run lifecycle -------------------------------------------

    def start_run(
        self,
        raw_goal: str,
        *,
        request_id: str,
        run_policy: RunPolicy | None = None,
        state: RunState = RunState.NORMALIZING,
    ) -> Run:
        import warnings

        warnings.warn(
            "OrchestrationService.start_run is deprecated; use create_run + "
            "start_and_drive (task 10.5).",
            DeprecationWarning,
            stacklevel=2,
        )
        with self.backend.unit_of_work() as uow:
            existing = uow.dedup.recall(request_id)
            if isinstance(existing, str):
                run = uow.runs.get(existing)
                if run is not None:
                    return run
            now = self.backend.clock.now()
            policy = run_policy or self.run_policy
            run = Run(
                run_id=self.backend.idgen.new_id("run"),
                raw_goal=raw_goal,
                state=state,
                policy=policy,
                created_at=now,
                updated_at=now,
            )
            uow.dedup.try_claim(request_id, run_id=run.run_id, kind="start_run")
            uow.dedup.remember(request_id, run.run_id)
            uow.runs.save(run, expected_version=1)
            uow.commit()
        return run

    def get_run(self, run_id: str) -> Run | None:
        with self.backend.unit_of_work() as uow:
            return uow.runs.get(run_id)

    def pause_run(self, run_id: str, *, expected_version: int) -> Run:
        """Transition to PAUSED and persist the safe continuation point (the
        phase the Run was paused from) so resume restores it deterministically
        rather than from a caller-supplied target (tasks 8.8/8.9)."""

        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.state_version != expected_version:
                raise StaleVersionError(f"run {run_id} expected {expected_version}")
            paused = run.transition(RunState.PAUSED, self.backend.clock.now()).model_copy(
                update={"paused_from_state": run.state}
            )
            uow.runs.save(paused, expected_version=run.state_version)
            uow.commit()
            return paused

    def resume_run(self, run_id: str, *, expected_version: int, target: RunState) -> Run:
        return self._transition(run_id, target, expected_version)

    def cancel_run(self, run_id: str, *, expected_version: int) -> Run:
        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.state_version != expected_version:
                raise StaleVersionError(f"run {run_id} expected {expected_version}")
            canceled = run.terminate(TerminationReason.CANCELED, self.backend.clock.now())
            uow.runs.save(canceled, expected_version=run.state_version)
            uow.commit()
            return canceled

    def _transition(self, run_id: str, target: RunState, expected_version: int) -> Run:
        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.state_version != expected_version:
                raise StaleVersionError(f"run {run_id} expected {expected_version}")
            moved = run.transition(target, self.backend.clock.now())
            uow.runs.save(moved, expected_version=run.state_version)
            uow.commit()
            return moved

    # --- 11.1 drive / tick ---------------------------------------------------

    def tick(self, run_id: str) -> RuntimeTick:
        _ = run_id
        return RuntimeTick(self.backend, executor=self._executor, limits=self.limits)

    async def drive_run(self, run_id: str, *, max_advances: int = 1000) -> AdvanceReport:
        """Loop RunCoordinator advances until the Run is terminal or explicitly
        blocked (task 10.4). Unlike the legacy Watch, a zero-dispatch tick no
        longer counts as blocked — IDLE phases (Goal/Plan/Finalize) progress."""

        if max_advances <= 0:
            raise ValueError("max_advances must be positive")
        last = await self.coordinator.advance(run_id)
        for _ in range(max_advances - 1):
            if last.disposition in (AdvanceDisposition.TERMINAL, AdvanceDisposition.BLOCKED):
                break
            if last.disposition is AdvanceDisposition.IDLE and not last.continue_immediately:
                break
            last = await self.coordinator.advance(run_id)
        return last

    # --- coordinator-backed public operations (tasks 10.2-10.4) -------------

    @property
    def coordinator(self):
        if self._coordinator is None:
            from agents_orchestration.orchestration.composition import (
                build_production_coordinator_from_settings,
            )

            self._coordinator = build_production_coordinator_from_settings(self.backend)
        return self._coordinator

    def create_run(self, raw_goal: str, *, request_id: str) -> Run:
        """Persist a CREATED Run only — no normalization, planning, or driving
        (task 10.3). Idempotent by Request ID like ``start_run``."""

        return self.start_run(raw_goal, request_id=request_id, state=RunState.CREATED)

    async def advance_run(self, run_id: str) -> AdvanceReport:
        """One bounded RunCoordinator advance (task 10.1)."""

        return await self.coordinator.advance(run_id)

    async def start_and_drive(self, raw_goal: str, *, request_id: str) -> Run:
        """Create-and-drive: persist a CREATED Run, drive the coordinator until
        terminal or blocked, return the freshly loaded Run view (task 10.4)."""

        run = self.create_run(raw_goal, request_id=request_id)
        await self.drive_run(run.run_id)
        result = self.get_run(run.run_id)
        if result is None:  # defensive: the run should not disappear mid-drive
            raise RuntimeError(f"run {run.run_id} disappeared after drive")
        return result

    async def start(self, command: StartRunCommand) -> Run:
        """Typed create-and-drive entry (task 10.2)."""

        return await self.start_and_drive(command.raw_goal, request_id=command.request_id)

    async def resume_and_drive(self, run_id: str, *, expected_version: int) -> Run:
        """Resume a paused Run from its persisted continuation (the phase it was
        paused from) and drive to terminal/blocked (tasks 8.10/8.11). The target
        comes solely from ``paused_from_state`` — callers cannot choose it.
        """

        with self.backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.state is not RunState.PAUSED:
                raise RuntimeError(f"run {run_id} is not paused (state={run.state.value})")
            if run.state_version != expected_version:
                raise StaleVersionError(f"run {run_id} expected {expected_version}")
            origin = run.paused_from_state or RunState.NORMALIZING
            resumed = run.transition(origin, self.backend.clock.now())
            uow.runs.save(resumed, expected_version=run.state_version)
            uow.commit()
        await self.drive_run(run_id)
        result = self.get_run(run_id)
        if result is None:
            raise RuntimeError(f"run {run_id} disappeared after resume")
        return result

    async def drive_run_legacy(self, run_id: str, *, max_ticks: int = 1000):
        """Deprecated Watch-based drive retained for compatibility (task 10.5)."""

        return await RuntimeWatch(self.backend, self.tick(run_id)).drive_run(
            run_id, max_ticks=max_ticks
        )

    # --- 11.5 gates ----------------------------------------------------------

    def list_gates(self, run_id: str):
        with self.backend.unit_of_work() as uow:
            return list(uow.gates.open_for_run(run_id))

    def respond_gate(
        self, gate_id: str, *, request_id: str, actor: str, role: str, payload: dict
    ):
        """Atomically consume a typed Gate response (tasks 3.1-3.4).

        Loads Gate + Run, validates the typed payload and claims the Request ID,
        resolves the persisted continuation, then either consumes normally
        (Gate RESPONDED + CONSUMED, Run amendment/transition with a single CAS
        save, durable resume/transition/terminal events) or invalidates the
        Gate (CANCELED + ``GATE_INVALIDATED``, Run unchanged, stable
        ``GateContinuationError``). ``advance_run``/``drive_run`` are never
        called here — the caller drives the next step explicitly (design 6).
        """

        from agents_orchestration.orchestration.gates import (
            GateContinuationError,
            GateService,
        )

        with self.backend.unit_of_work() as uow:
            gate = uow.gates.get(gate_id)
            if gate is None:
                raise KeyError(gate_id)
            run = uow.runs.get(gate.run_id)
            if run is None:
                raise KeyError(gate.run_id)
            original_version = run.state_version
            svc = GateService(uow, self.backend.clock, self.backend.idgen)
            validated = svc.validate_response(
                gate, request_id=request_id, actor=actor, role=role, payload=payload
            )
            now = self.backend.clock.now()
            resolution = resolve_gate_continuation(gate, run, validated.outcome)

            if resolution.outcome not in (
                ContinuationOutcome.APPLIED,
                ContinuationOutcome.SAME_STATE,
            ):
                self._invalidate_gate(uow, gate, run, validated, resolution, now)
                uow.commit()
                raise GateContinuationError(
                    f"gate {gate.gate_id} invalidated: {resolution.outcome.value}"
                )

            responded = gate.respond(
                request_id=request_id, actor=actor, payload=payload, at=now
            )
            consumed = responded.consume(now)
            final_run = None
            replanned = False
            if (
                gate.gate_type is GateType.CONFLICT_RESOLUTION
                and validated.outcome == "resolved"
                and gate.continuation is not None
                and gate.continuation.intent
                is GateContinuationIntent.REVIEW_RESEARCH_GAP
            ):
                # analyze-sufficiency-feedback 7.2/7.3: a "continue research"
                # continuation runs the SAME shared Focused Replan as ANALYZE
                # (or terminates deterministically when budget is exhausted),
                # instead of a bare state jump back to RESEARCHING.
                final_run, terminated = self._apply_research_gap_continuation(uow, run, gate, now)
                replanned = not terminated
                if terminated:
                    uow.runs.save(final_run, expected_version=original_version)
                # the focused-replan path already CAS-saved final_run itself
            elif (
                gate.gate_type is GateType.PLAN_APPROVAL
                and validated.outcome == "approved"
            ):
                pending = uow.plans.current(run.run_id)
                if pending is not None and pending.acceptance is PlanAcceptance.PROPOSED:
                    graph = pending.graph
                    proposal = PlanProposal(
                        run_id=run.run_id,
                        plan_id=graph.plan_id,
                        task_specs=graph.task_specs,
                        dependencies=graph.dependencies,
                        deliverable_paths=graph.deliverable_paths,
                    )
                    _plan, final_run = PlanAcceptor(
                        uow, self.backend.clock, self.backend.idgen
                    ).accept(
                        run,
                        proposal,
                        PlanValidation(accepted=True, diagnostics=(), graph=graph),
                    )
            if final_run is None:
                final_run = self._amend_run_for_gate(run, resolution, validated, now)
                uow.runs.save(final_run, expected_version=original_version)
            uow.gates.save(consumed)
            uow.events.append(
                self._gate_consumption_events(
                    gate, consumed, validated, run, final_run, now, replanned=replanned
                )
            )
            uow.commit()
            return consumed

    def _invalidate_gate(self, uow, gate, run, validated, resolution, now) -> None:
        """Fail a Gate whose continuation cannot be safely applied (task 3.3).

        Gate -> CANCELED + ``GATE_INVALIDATED`` with the classified reason; the
        Run and its target information are left untouched.
        """

        canceled = gate.cancel()
        uow.gates.save(canceled)
        uow.events.append(
            [
                DomainEvent(
                    event_id=self.backend.idgen.new_id("evt"),
                    run_id=gate.run_id,
                    effect=EffectType.GATE_INVALIDATED,
                    state_version=run.state_version,
                    occurred_at=now,
                    gate_id=gate.gate_id,
                    plan_version=gate.plan_version,
                    payload={
                        "gate_type": gate.gate_type.value,
                        "outcome": validated.outcome,
                        "reason": resolution.outcome.value,
                        "detail": resolution.reason,
                    },
                )
            ]
        )

    def _amend_run_for_gate(self, run, resolution, validated, now):
        """Build the final Run from the original in one version bump (task 3.2/4.2).

        APPLIED transitions (or terminates for cancelled/escalated); SAME_STATE
        bumps the version in place; a clarified outcome additionally stores the
        goal clarification. The original Run is never mutated.
        """

        if resolution.outcome is ContinuationOutcome.APPLIED:
            target = resolution.target_state
            if target.is_terminal:
                reason = (
                    TerminationReason.CANCELED
                    if target is RunState.CANCELED
                    else TerminationReason.FAILED
                )
                base = run.terminate(reason, now)
            else:
                base = run.transition(target, now)
        else:  # SAME_STATE
            base = run.bump_version(now)
        if validated.clarification:
            base = base.model_copy(update={"goal_clarification": validated.clarification})
        return base

    def _apply_research_gap_continuation(
        self, uow, run: Run, gate, now
    ) -> tuple[Run, bool]:
        """CONFLICT_RESOLUTION 'resolved' continuation (tasks 7.2 / 7.3).

        Uses the persisted, length-bounded REVIEW feedback as the gap hint for
        the SAME FocusedReplanBuilder + atomic
        ``replan_and_transition`` that ANALYZE uses. If the shared replan budget
        is already exhausted, the Run terminates with REQUIRED_EVIDENCE_MISSING
        and no Plan/Task is created. Returns ``(final_run, terminated)``.
        """

        continuation = gate.continuation
        if continuation is None or not continuation.feedback:
            raise ValueError("review research-gap continuation is missing persisted feedback")
        gap_hint = continuation.feedback
        if run.replan_count >= run.policy.max_replans:
            return run.terminate(TerminationReason.REQUIRED_EVIDENCE_MISSING, now), True
        builder = FocusedReplanBuilder(
            self.capability_registry.allowed_kinds(), self.backend.idgen
        )
        focused = builder.build(
            run_id=run.run_id,
            objective=run.effective_goal,
            approved_research_capabilities=self._approved_research_capabilities(uow, run),
            gap_hint=gap_hint,
        )
        correlation = {
            "gap_id": continuation.correlation_id or focused.gap.gap_id,
            "focus_hash": focused.gap.focus_hash,
            "source_phase": "review",
            "source_state_version": run.state_version,
        }
        _plan, new_run = ReplanService(
            uow,
            PlanValidator(self.limits),
            PlanAcceptor(uow, self.backend.clock, self.backend.idgen),
            self.backend.clock,
            self.backend.idgen,
        ).replan_and_transition(
            run,
            focused.proposal,
            transition_to=RunState.RESEARCHING,
            correlation=correlation,
            now=now,
        )
        return new_run, False

    def _approved_research_capabilities(self, uow, run: Run):
        capabilities: list = []
        for task in uow.tasks.by_run(run.run_id, plan_version=run.current_plan_version):
            if task.worker_role is WorkerRole.EVIDENCE_RESEARCHER:
                capabilities.extend(task.required_capabilities)
        return tuple(dict.fromkeys(capabilities))

    def _gate_consumption_events(
        self, gate, consumed, validated, original_run, final_run, now, *, replanned: bool = False
    ) -> list[DomainEvent]:
        """Durable events for a successful Gate consumption (task 3.4 / 6)."""

        idgen = self.backend.idgen
        common: dict[str, object] = {
            "run_id": gate.run_id,
            "occurred_at": now,
            "gate_id": gate.gate_id,
            "plan_version": gate.plan_version,
        }
        events = [
            DomainEvent(
                event_id=idgen.new_id("evt"),
                effect=EffectType.GATE_RESPONDED,
                state_version=final_run.state_version,
                payload={"gate_type": gate.gate_type.value},
                **common,
            ),
            DomainEvent(
                event_id=idgen.new_id("evt"),
                effect=EffectType.GATE_CONSUMED,
                state_version=final_run.state_version,
                payload={"gate_type": gate.gate_type.value},
                **common,
            ),
            DomainEvent(
                event_id=idgen.new_id("evt"),
                effect=EffectType.RUN_RESUMED,
                state_version=final_run.state_version,
                payload={
                    "gate_type": gate.gate_type.value,
                    "outcome": validated.outcome,
                    "from_state_version": original_run.state_version,
                    "to_state_version": final_run.state_version,
                },
                **common,
            ),
        ]
        if not replanned and final_run.state is not original_run.state:
            events.append(
                DomainEvent(
                    event_id=idgen.new_id("evt"),
                    effect=EffectType.RUN_STATE_TRANSITION,
                    state_version=final_run.state_version,
                    payload={
                        "from_state": original_run.state.value,
                        "to_state": final_run.state.value,
                        "gate_type": gate.gate_type.value,
                        "outcome": validated.outcome,
                    },
                    **common,
                )
            )
        if final_run.state.is_terminal:
            events.append(
                DomainEvent(
                    event_id=idgen.new_id("evt"),
                    effect=EffectType.RUN_TERMINATED,
                    state_version=final_run.state_version,
                    payload={
                        "state": final_run.state.value,
                        "termination": final_run.termination.value
                        if final_run.termination
                        else None,
                        "gate_type": gate.gate_type.value,
                        "outcome": validated.outcome,
                    },
                    **common,
                )
            )
        return events

    # --- 11.6 artifacts ------------------------------------------------------

    def list_artifacts(self) -> list[ArtifactRef]:
        with self.backend.unit_of_work() as uow:
            return list(uow.artifacts.list_all())

    def export_artifact(self, artifact_id: str) -> ExportedArtifact:
        with self.backend.unit_of_work() as uow:
            ref = uow.artifacts.get_by_id(artifact_id)
            if ref is None:
                raise KeyError(artifact_id)
            try:
                data = uow.artifacts.read(ref)
            except OrphanArtifactError as exc:
                raise KeyError(artifact_id) from exc
            return ExportedArtifact(
                ref.artifact_id, ref.content_hash, ref.path, ref.size_bytes, data
            )

    # --- 11.7 capabilities ---------------------------------------------------

    def list_capabilities(self):
        return self.capability_registry.descriptors()

    def capability_doctor(self):
        return capability_doctor(self.capability_registry)


__all__ = [
    "DefaultWorkerHandler",
    "DuplicateStartError",
    "ExportedArtifact",
    "OrchestrationService",
    "StartRunCommand",
]

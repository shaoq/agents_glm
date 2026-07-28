"""Application use-case service and composition root (tasks 11.1 / 11.2).

The CLI is a thin adapter over :class:`OrchestrationService`; all domain logic
lives here and below. ``start_run`` is idempotent by Request ID and reports stale
Expected Version conflicts (task 11.2). The service composes the durable runtime,
planning, gates, capabilities and report layers built in Sections 3-10.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_orchestration.adapters.fake import build_fake_registry
from agents_orchestration.adapters.health import capability_doctor
from agents_orchestration.capabilities.registry import CapabilityRegistry
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.artifact import ArtifactRef
from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.coordination import AdvanceDisposition, AdvanceReport
from agents_orchestration.domain.enums import RunState, TerminationReason, WorkerRole
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.orchestration.composition import build_offline_coordinator
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


class DefaultWorkerHandler:
    """Invoke the task's first registered capability and wrap it in a TaskResult.

    Real per-role prompt logic is layered on later; this keeps a default run
    executable with Fake adapters and proves the wiring end to end.
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
    ) -> None:
        self.backend = backend
        self.limits = limits or SystemLimits()
        self.capability_registry = capability_registry or build_fake_registry()
        self.run_policy = run_policy or RunPolicy.from_limits(self.limits)
        self.workers = WorkerRegistry.default()
        self.router = CapabilityRouter(self.capability_registry, backend.idgen)
        handler = DefaultWorkerHandler(self.capability_registry, backend.idgen)
        self._handlers = {role: handler for role in WorkerRole}
        self._executor = WorkerExecutor(self.workers, self.router, self._handlers, self.run_policy)
        self._coordinator = None

    # --- 11.1 / 11.2 run lifecycle -------------------------------------------

    def start_run(
        self,
        raw_goal: str,
        *,
        request_id: str,
        run_policy: RunPolicy | None = None,
        state: RunState = RunState.NORMALIZING,
    ) -> Run:
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
        return self._transition(run_id, RunState.PAUSED, expected_version)

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
            last = await self.coordinator.advance(run_id)
        return last

    # --- coordinator-backed public operations (tasks 10.2-10.4) -------------

    @property
    def coordinator(self):
        if self._coordinator is None:
            self._coordinator = build_offline_coordinator(self.backend)
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

    async def drive_run_legacy(self, run_id: str, *, max_ticks: int = 1000):
        """Deprecated Watch-based drive retained for compatibility (task 10.5)."""

        return await RuntimeWatch(self.backend, self.tick(run_id)).drive_run(
            run_id, max_ticks=max_ticks
        )

    # --- 11.5 gates ----------------------------------------------------------

    def list_gates(self, run_id: str):
        with self.backend.unit_of_work() as uow:
            return list(uow.gates.open_for_run(run_id))

    def respond_gate(self, gate_id: str, *, request_id: str, actor: str, role: str, payload: dict):
        from agents_orchestration.orchestration.gates import GateService

        with self.backend.unit_of_work() as uow:
            gate = uow.gates.get(gate_id)
            if gate is None:
                raise KeyError(gate_id)
            svc = GateService(uow, self.backend.clock, self.backend.idgen)
            responded = svc.respond(
                gate,
                request_id=request_id,
                actor=actor,
                role=role,
                payload=payload,
            )
            consumed = svc.consume(responded)
            uow.commit()
            return consumed

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
]

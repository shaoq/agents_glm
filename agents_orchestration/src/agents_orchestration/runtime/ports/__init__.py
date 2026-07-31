"""Port protocols for the durable runtime (design Decision 4 / task 3.1).

The runtime core depends only on these Protocols; the SQLite implementations in
:mod:`agents_orchestration.runtime.persistence` are the infrastructure adapters.
This keeps the deterministic runtime testable without a database and lets a
future HTTP/Queue Runtime swap implementations.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.evidence import Evidence
from agents_orchestration.domain.execution import Attempt, Operation, Run, Task
from agents_orchestration.domain.goal import CompletionContract, GoalSpec
from agents_orchestration.domain.lifecycle import Checkpoint, Gate, Lease
from agents_orchestration.domain.plan import Dependency, Plan
from agents_orchestration.domain.research_loop import (
    ResearchDirection,
    ResearchLoop,
    ResearchStep,
    ResearchStepStatus,
)


class StaleVersionError(RuntimeError):
    """A compare-and-set update affected 0 rows (state version mismatch)."""


class ConcurrencyError(RuntimeError):
    """A lease/unique constraint was violated by a concurrent writer."""


class OrphanArtifactError(RuntimeError):
    """An artifact file exists but is not referenced by any metadata row."""


@runtime_checkable
class Clock(Protocol):
    """Deterministic time source (never ``datetime.now`` in the runtime core)."""

    def now(self) -> datetime: ...


@runtime_checkable
class IDGenerator(Protocol):
    """Opaque id factory injected so ids can be deterministic / ordered in tests."""

    def new_id(self, prefix: str) -> str: ...


@runtime_checkable
class RunRepository(Protocol):
    def get(self, run_id: str) -> Run | None: ...
    def save(self, run: Run, expected_version: int) -> None: ...
    def list_resumable(self) -> Sequence[Run]: ...


@runtime_checkable
class TaskRepository(Protocol):
    def get(self, task_id: str) -> Task | None: ...
    def save(self, task: Task) -> None: ...
    def by_run(self, run_id: str, plan_version: int | None = None) -> Sequence[Task]: ...
    def materialize(self, tasks: Sequence[Task]) -> None: ...


@runtime_checkable
class AttemptRepository(Protocol):
    def get(self, attempt_id: str) -> Attempt | None: ...
    def save(self, attempt: Attempt) -> None: ...
    def active_for_task(self, task_id: str) -> Attempt | None: ...
    def by_run(self, run_id: str) -> Sequence[Attempt]: ...


@runtime_checkable
class PlanRepository(Protocol):
    def get(self, run_id: str, version: int) -> Plan | None: ...
    def current(self, run_id: str) -> Plan | None: ...
    def save(self, plan: Plan) -> None: ...


@runtime_checkable
class GoalRepository(Protocol):
    def save(self, run_id: str, goal: GoalSpec) -> None: ...
    def get(self, run_id: str) -> GoalSpec | None: ...


@runtime_checkable
class CompletionContractRepository(Protocol):
    def save(self, run_id: str, contract: CompletionContract) -> None: ...
    def get(self, run_id: str) -> CompletionContract | None: ...


@runtime_checkable
class DependencyRepository(Protocol):
    def save(self, run_id: str, plan_version: int, deps: Sequence[Dependency]) -> None: ...
    def by_plan(self, run_id: str, plan_version: int) -> Sequence[Dependency]: ...


@runtime_checkable
class LeaseRepository(Protocol):
    def get(self, task_id: str) -> Lease | None: ...
    def save(self, lease: Lease, expected_epoch: int | None = None) -> None: ...
    def active(self) -> Sequence[Lease]: ...


@runtime_checkable
class GateRepository(Protocol):
    def get(self, gate_id: str) -> Gate | None: ...
    def save(self, gate: Gate) -> None: ...
    def open_for_run(self, run_id: str) -> Sequence[Gate]: ...


@runtime_checkable
class CheckpointRepository(Protocol):
    def save(self, checkpoint: Checkpoint) -> None: ...
    def latest(self, run_id: str) -> Checkpoint | None: ...


@runtime_checkable
class OperationRepository(Protocol):
    def save(self, operation: Operation) -> None: ...
    def get(self, operation_id: str) -> Operation | None: ...
    def by_attempt(self, attempt_id: str) -> Sequence[Operation]: ...


@runtime_checkable
class ResearchLoopRepository(Protocol):
    def get(self, loop_id: str) -> ResearchLoop | None: ...
    def for_task(self, run_id: str, plan_version: int, task_id: str) -> ResearchLoop | None: ...
    def by_run(self, run_id: str, plan_version: int) -> Sequence[ResearchLoop]: ...
    def save(self, loop: ResearchLoop, expected_version: int | None) -> None: ...


@runtime_checkable
class ResearchDirectionRepository(Protocol):
    def get(self, direction_id: str) -> ResearchDirection | None: ...
    def by_loop(self, loop_id: str) -> Sequence[ResearchDirection]: ...
    def by_focus_hash(self, loop_id: str, focus_hash: str) -> ResearchDirection | None: ...
    def save(self, direction: ResearchDirection) -> None: ...


@runtime_checkable
class ResearchStepRepository(Protocol):
    def get(self, step_id: str) -> ResearchStep | None: ...
    def by_loop(self, loop_id: str) -> Sequence[ResearchStep]: ...
    def active_for_task(
        self, run_id: str, plan_version: int, task_id: str
    ) -> ResearchStep | None: ...
    def by_logical_key(
        self, run_id: str, plan_version: int, task_id: str, step_index: int
    ) -> ResearchStep | None: ...
    def save(self, step: ResearchStep, expected_status: ResearchStepStatus | None) -> None: ...


@runtime_checkable
class EventStore(Protocol):
    def append(self, events: Sequence[DomainEvent]) -> None: ...
    def stream(
        self, run_id: str, *, after_state_version: int | None = None
    ) -> Iterator[DomainEvent]: ...


@runtime_checkable
class Outbox(Protocol):
    def enqueue(self, run_id: str, events: Sequence[DomainEvent]) -> None: ...
    def pending(self) -> Sequence[tuple[int, DomainEvent]]: ...
    def mark_published(self, outbox_id: int) -> None: ...


@runtime_checkable
class RequestDedupStore(Protocol):
    def try_claim(self, request_id: str, *, run_id: str, kind: str) -> bool: ...
    def remember(self, request_id: str, result: object) -> None: ...
    def recall(self, request_id: str) -> object | None: ...


@runtime_checkable
class ArtifactStore(Protocol):
    def write(
        self, content: bytes, *, kind: ArtifactKind, artifact_id: str | None = None
    ) -> ArtifactRef: ...
    def read(self, ref: ArtifactRef) -> bytes: ...
    def find(self, content_hash: str) -> ArtifactRef | None: ...
    def get_by_id(self, artifact_id: str) -> ArtifactRef | None: ...
    def list_all(self) -> Sequence[ArtifactRef]: ...
    def record_metadata(self, ref: ArtifactRef) -> None: ...
    def referenced_hashes(self) -> set[str]: ...
    def list_orphans(self) -> Sequence[Path]: ...
    def delete_orphan(self, path: Path) -> None: ...


@runtime_checkable
@runtime_checkable
class EvidenceStore(Protocol):
    """Persisted accepted evidence read by downstream phases (Research Join / Analysis)."""

    def save_many(
        self, run_id: str, attempt_id: str | None, evidences: Sequence[Evidence]
    ) -> None: ...

    def by_run(self, run_id: str) -> Sequence[Evidence]: ...


class UnitOfWork(Protocol):
    """One atomic transaction spanning state, events, checkpoint and outbox."""

    runs: RunRepository
    tasks: TaskRepository
    attempts: AttemptRepository
    plans: PlanRepository
    goals: GoalRepository
    completion: CompletionContractRepository
    dependencies: DependencyRepository
    leases: LeaseRepository
    gates: GateRepository
    checkpoints: CheckpointRepository
    events: EventStore
    outbox: Outbox
    operations: OperationRepository
    research_loops: ResearchLoopRepository
    research_directions: ResearchDirectionRepository
    research_steps: ResearchStepRepository
    artifacts: ArtifactStore
    dedup: RequestDedupStore
    evidence: EvidenceStore

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@runtime_checkable
class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


def _environ_now() -> datetime:  # pragma: no cover - tiny helper
    """Used only by the system clock; tests inject a fake Clock."""

    epoch = float(os.environ.get("ORCH_FIXED_NOW", "0") or 0)
    if epoch:
        return datetime.fromtimestamp(epoch)
    return datetime.now()


class SystemClock:
    """Default Clock implementation backed by the wall clock."""

    def now(self) -> datetime:
        return _environ_now()


class UUIDIDGenerator:
    """Default IDGenerator backed by :func:`agents_orchestration.domain.ids.new_id`."""

    def new_id(self, prefix: str) -> str:
        from agents_orchestration.domain.ids import new_id

        return new_id(prefix)

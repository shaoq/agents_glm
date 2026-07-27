"""RecoveryManager: expire stale claims, inspect unknown calls, rebuild ready
work (task 4.6). Run at the start of every Tick and on process restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_orchestration.domain.enums import AttemptAcceptance, AttemptState, TaskState
from agents_orchestration.domain.execution import OutcomeCertainty
from agents_orchestration.domain.lifecycle import Lease
from agents_orchestration.runtime.lease import LeaseManager


@dataclass(frozen=True)
class RecoveryReport:
    expired_leases: tuple[Lease, ...] = field(default_factory=tuple)
    requeued_tasks: tuple[str, ...] = field(default_factory=tuple)
    unknown_operations: tuple[str, ...] = field(default_factory=tuple)


class RecoveryManager:
    def __init__(self, uow, lease_manager: LeaseManager, clock) -> None:
        self.uow = uow
        self.lease_manager = lease_manager
        self.clock = clock

    def recover(self, run_id: str, *, now) -> RecoveryReport:
        expired = self.lease_manager.expire_stale(now)
        requeued: list[str] = []
        for lease in expired:
            task = self.uow.tasks.get(lease.task_id)
            if task is None or task.run_id != run_id:
                continue
            if task.state is not TaskState.DISPATCHED:
                continue
            active = self.uow.attempts.active_for_task(lease.task_id)
            if active is not None:
                self.uow.attempts.save(
                    active.model_copy(
                        update={
                            "state": AttemptState.EXPIRED,
                            "acceptance": AttemptAcceptance.REJECTED_STALE_LEASE,
                            "finished_at": now,
                        }
                    )
                )
            requeued_task = task.transition(TaskState.PENDING, now)
            self.uow.tasks.save(requeued_task)
            requeued.append(lease.task_id)

        unknown_ops = [
            op.operation_id
            for op in self._operations_for_run(run_id)
            if op.outcome_certainty is OutcomeCertainty.UNKNOWN and op.finished_at is None
        ]
        return RecoveryReport(
            expired_leases=tuple(expired),
            requeued_tasks=tuple(requeued),
            unknown_operations=tuple(unknown_ops),
        )

    def _operations_for_run(self, run_id: str):
        attempt_ids = {a.attempt_id for a in self.uow.attempts.by_run(run_id)}
        return [op for op in (self.uow.operations.by_attempt(a) for a in attempt_ids) for op in op]

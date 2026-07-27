"""Attempt result validation and Late Result handling (tasks 4.7 / 4.8).

A tendered result is accepted only when it is still the active dispatch for the
current Plan, State and Lease epoch and the Task has not been superseded. Late,
stale or superseded results are rejected but retained as Observation events so
restart recovery and audit can explain them (design Decision 3 / task 4.8).
"""

from __future__ import annotations

from agents_orchestration.domain.enums import AttemptAcceptance, EffectType
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Attempt, Run
from agents_orchestration.domain.state_machine import attempt_acceptance


class AttemptValidator:
    """Full fencing validation for a tendered attempt result (task 4.7)."""

    def __init__(self, uow) -> None:
        self.uow = uow

    def validate(
        self, attempt: Attempt, *, run: Run, current_plan_version: int
    ) -> AttemptAcceptance:
        active = self.uow.attempts.active_for_task(attempt.task_id)
        is_active = active is not None and active.attempt_id == attempt.attempt_id
        task = self.uow.tasks.get(attempt.task_id)
        lease = self.uow.leases.get(attempt.task_id)
        lease_matches = (
            lease is not None and lease.epoch == attempt.lease_epoch and lease.state.is_active
        )
        # state_matches: the Task is still in active execution for this dispatch.
        from agents_orchestration.domain.enums import TaskState

        state_matches = task is not None and task.state is TaskState.DISPATCHED
        not_superseded = task is not None and task.state is not TaskState.SUPERSEDED
        return attempt_acceptance(
            attempt.state,
            is_active=is_active,
            plan_matches=attempt.plan_version == current_plan_version,
            state_matches=state_matches,
            lease_matches=lease_matches,
            not_superseded=not_superseded,
        )


def record_late_observation(
    uow,
    *,
    attempt: Attempt,
    acceptance: AttemptAcceptance,
    state_version: int,
    clock,
    idgen,
) -> DomainEvent:
    """Retain a rejected (late/stale/superseded) result as an Observation event.

    State is not changed; the observation is preserved for recovery and audit
    (task 4.8).
    """

    event = DomainEvent(
        event_id=idgen.new_id("evt"),
        run_id=attempt.run_id,
        effect=EffectType.ATTEMPT_REJECTED,
        state_version=state_version,
        occurred_at=clock.now(),
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        payload={"acceptance": acceptance.value, "late": True},
    )
    uow.events.append([event])
    return event

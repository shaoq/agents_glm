"""Deterministic state transition validation (design Decision 2 / tasks 2.5-2.8).

The runtime never allows a model to bypass these rules: every formal transition
is validated here before it is committed. Invalid transitions raise
:class:`StateTransitionError` and produce no state change.
"""

from __future__ import annotations

from agents_orchestration.domain.enums import (
    AttemptAcceptance,
    AttemptState,
    GateState,
    RunState,
    TaskState,
)


class StateTransitionError(ValueError):
    """Raised when a requested state transition violates the deterministic rules."""


# --- Run lifecycle -----------------------------------------------------------

_RUN_PHASE_FORWARD: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.NORMALIZING, RunState.CANCELED}),
    RunState.NORMALIZING: frozenset(
        {RunState.PLANNING, RunState.PAUSED, RunState.CANCELED, RunState.FAILED}
    ),
    RunState.PLANNING: frozenset(
        {
            RunState.AWAITING_PLAN_APPROVAL,
            RunState.RESEARCHING,
            RunState.PAUSED,
            RunState.CANCELED,
            RunState.FAILED,
        }
    ),
    RunState.AWAITING_PLAN_APPROVAL: frozenset(
        {
            RunState.RESEARCHING,
            RunState.PLANNING,
            RunState.PAUSED,
            RunState.CANCELED,
            RunState.FAILED,
        }
    ),
    RunState.RESEARCHING: frozenset(
        {RunState.ANALYZING, RunState.PLANNING, RunState.PAUSED, RunState.CANCELED, RunState.FAILED}
    ),
    RunState.ANALYZING: frozenset(
        {
            RunState.WRITING,
            RunState.RESEARCHING,
            RunState.PLANNING,
            RunState.PAUSED,
            RunState.CANCELED,
            RunState.FAILED,
        }
    ),
    RunState.WRITING: frozenset(
        {RunState.REVIEWING, RunState.PAUSED, RunState.CANCELED, RunState.FAILED}
    ),
    RunState.REVIEWING: frozenset(
        {
            RunState.RESEARCHING,
            RunState.WRITING,
            RunState.AWAITING_FINAL_REVIEW,
            RunState.FINALIZING,
            RunState.PLANNING,
            RunState.PAUSED,
            RunState.CANCELED,
            RunState.FAILED,
        }
    ),
    RunState.AWAITING_FINAL_REVIEW: frozenset(
        {
            RunState.FINALIZING,
            RunState.REVIEWING,
            RunState.PAUSED,
            RunState.CANCELED,
            RunState.FAILED,
        }
    ),
    RunState.FINALIZING: frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED}),
}

_RUN_RESUMABLE: frozenset[RunState] = frozenset(
    {
        RunState.NORMALIZING,
        RunState.PLANNING,
        RunState.RESEARCHING,
        RunState.ANALYZING,
        RunState.WRITING,
        RunState.REVIEWING,
        RunState.FINALIZING,
    }
)

_RUN_TERMINAL: frozenset[RunState] = frozenset(
    {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED}
)


def run_transitions(current: RunState) -> frozenset[RunState]:
    """All states reachable from ``current`` in one step."""

    if current is RunState.PAUSED:
        return _RUN_RESUMABLE | {RunState.CANCELED}
    return _RUN_PHASE_FORWARD.get(current, frozenset())


def can_transition_run(current: RunState, target: RunState) -> bool:
    return target in run_transitions(current)


def assert_run_transition(current: RunState, target: RunState) -> None:
    if not can_transition_run(current, target):
        raise StateTransitionError(f"Run cannot transition {current.value} -> {target.value}")


def can_pause(current: RunState) -> bool:
    return current in _RUN_RESUMABLE


def can_resume(current: RunState) -> bool:
    return current is RunState.PAUSED


# --- Task lifecycle ----------------------------------------------------------

_TASK_FORWARD: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.READY, TaskState.CANCELED, TaskState.SKIPPED}),
    TaskState.READY: frozenset({TaskState.DISPATCHED, TaskState.CANCELED, TaskState.SKIPPED}),
    TaskState.DISPATCHED: frozenset(
        {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.AWAITING_RETRY,
            TaskState.PENDING,
            TaskState.CANCELED,
        }
    ),
    TaskState.AWAITING_RETRY: frozenset(
        {TaskState.READY, TaskState.DISPATCHED, TaskState.CANCELED}
    ),
    TaskState.FAILED: frozenset({TaskState.SUPERSEDED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.SUPERSEDED: frozenset(),
    TaskState.SKIPPED: frozenset(),
    TaskState.CANCELED: frozenset(),
}


def can_transition_task(current: TaskState, target: TaskState) -> bool:
    return target in _TASK_FORWARD.get(current, frozenset())


def assert_task_transition(current: TaskState, target: TaskState) -> None:
    if not can_transition_task(current, target):
        raise StateTransitionError(f"Task cannot transition {current.value} -> {target.value}")


# --- Attempt acceptance ------------------------------------------------------


def attempt_acceptance(
    state: AttemptState,
    *,
    is_active: bool,
    plan_matches: bool,
    state_matches: bool,
    lease_matches: bool,
    not_superseded: bool,
) -> AttemptAcceptance:
    """Classify a tendered attempt result (task 2.7; full fencing in 4.7/4.8).

    A result is ``ACCEPTED`` only when the attempt is still the active dispatch
    for the current Plan/State/Lease and the Task has not been superseded. Late,
    stale or superseded results are rejected but retained as observations.
    """

    if state is not AttemptState.DISPATCHED:
        return AttemptAcceptance.REJECTED_LATE
    if not is_active:
        return AttemptAcceptance.REJECTED_LATE
    if not lease_matches:
        return AttemptAcceptance.REJECTED_STALE_LEASE
    if not plan_matches:
        return AttemptAcceptance.REJECTED_STALE_PLAN
    if not state_matches:
        return AttemptAcceptance.REJECTED_STALE_STATE
    if not not_superseded:
        return AttemptAcceptance.REJECTED_SUPERSEDED
    return AttemptAcceptance.ACCEPTED


# --- Gate single-use consumption --------------------------------------------


def can_respond_gate(state: GateState) -> bool:
    return state is GateState.OPEN


def can_consume_gate(state: GateState) -> bool:
    return state is GateState.RESPONDED


def assert_gate_respond(state: GateState) -> None:
    if not can_respond_gate(state):
        raise StateTransitionError(f"Gate in {state.value} cannot be responded to")


def assert_gate_consume(state: GateState) -> None:
    if not can_consume_gate(state):
        raise StateTransitionError(
            f"Gate in {state.value} cannot be consumed (single-use violation)"
        )

"""Unit tests for deterministic state machine validation (tasks 2.5-2.8)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import (
    AttemptAcceptance,
    AttemptState,
    GateState,
    RunState,
    TaskState,
)
from agents_orchestration.domain.state_machine import (
    StateTransitionError,
    assert_gate_consume,
    assert_gate_respond,
    assert_run_transition,
    assert_task_transition,
    attempt_acceptance,
    can_consume_gate,
    can_pause,
    can_resume,
    can_transition_run,
    can_transition_task,
    run_transitions,
)

# --- 2.5 Run state transitions ----------------------------------------------


@pytest.mark.unit
def test_run_created_can_normalize_or_cancel() -> None:
    assert can_transition_run(RunState.CREATED, RunState.NORMALIZING)
    assert can_transition_run(RunState.CREATED, RunState.CANCELED)


@pytest.mark.unit
def test_run_cannot_skip_phases() -> None:
    assert not can_transition_run(RunState.CREATED, RunState.RESEARCHING)
    assert not can_transition_run(RunState.NORMALIZING, RunState.FINALIZING)


@pytest.mark.unit
def test_run_assert_invalid_raises() -> None:
    with pytest.raises(StateTransitionError):
        assert_run_transition(RunState.CREATED, RunState.RESEARCHING)


@pytest.mark.unit
def test_run_terminal_states_are_terminal() -> None:
    for terminal in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED):
        assert run_transitions(terminal) == frozenset()


@pytest.mark.unit
def test_run_pause_and_resume() -> None:
    assert can_pause(RunState.RESEARCHING)
    assert not can_pause(RunState.CREATED)
    assert not can_pause(RunState.PAUSED)
    assert can_resume(RunState.PAUSED)
    assert not can_resume(RunState.RESEARCHING)


@pytest.mark.unit
def test_run_paused_can_resume_into_phases_or_cancel() -> None:
    targets = run_transitions(RunState.PAUSED)
    assert RunState.RESEARCHING in targets
    assert RunState.FINALIZING in targets
    assert RunState.CANCELED in targets
    assert RunState.CREATED not in targets
    assert RunState.PAUSED not in targets


# --- 2.6 Task state transitions ---------------------------------------------


@pytest.mark.unit
def test_task_happy_path() -> None:
    assert can_transition_task(TaskState.PENDING, TaskState.READY)
    assert can_transition_task(TaskState.READY, TaskState.DISPATCHED)
    assert can_transition_task(TaskState.DISPATCHED, TaskState.SUCCEEDED)
    assert can_transition_task(TaskState.DISPATCHED, TaskState.FAILED)


@pytest.mark.unit
def test_task_cannot_short_circuit() -> None:
    assert not can_transition_task(TaskState.PENDING, TaskState.SUCCEEDED)
    assert not can_transition_task(TaskState.READY, TaskState.SUCCEEDED)


@pytest.mark.unit
def test_task_failed_can_be_superseded_only() -> None:
    assert can_transition_task(TaskState.FAILED, TaskState.SUPERSEDED)
    assert not can_transition_task(TaskState.FAILED, TaskState.READY)


@pytest.mark.unit
def test_task_assert_invalid_raises() -> None:
    with pytest.raises(StateTransitionError):
        assert_task_transition(TaskState.PENDING, TaskState.SUCCEEDED)


@pytest.mark.unit
def test_task_accepted_states_are_terminal() -> None:
    for terminal in (
        TaskState.SUCCEEDED,
        TaskState.SUPERSEDED,
        TaskState.SKIPPED,
        TaskState.CANCELED,
    ):
        assert not can_transition_task(terminal, TaskState.READY)


# --- 2.7 Attempt result acceptance ------------------------------------------


@pytest.mark.unit
def test_attempt_accepted_when_all_checks_pass() -> None:
    acceptance = attempt_acceptance(
        AttemptState.DISPATCHED,
        is_active=True,
        plan_matches=True,
        state_matches=True,
        lease_matches=True,
        not_superseded=True,
    )
    assert acceptance is AttemptAcceptance.ACCEPTED


@pytest.mark.unit
def test_attempt_rejected_late_when_not_dispatched() -> None:
    acceptance = attempt_acceptance(
        AttemptState.SUCCEEDED,
        is_active=True,
        plan_matches=True,
        state_matches=True,
        lease_matches=True,
        not_superseded=True,
    )
    assert acceptance is AttemptAcceptance.REJECTED_LATE


@pytest.mark.unit
def test_attempt_rejected_for_stale_lease_plan_state_or_supersession() -> None:
    common = dict(
        is_active=True,
        plan_matches=True,
        state_matches=True,
        lease_matches=True,
        not_superseded=True,
    )

    assert (
        attempt_acceptance(AttemptState.DISPATCHED, **{**common, "lease_matches": False})
        is AttemptAcceptance.REJECTED_STALE_LEASE
    )
    assert (
        attempt_acceptance(AttemptState.DISPATCHED, **{**common, "plan_matches": False})
        is AttemptAcceptance.REJECTED_STALE_PLAN
    )
    assert (
        attempt_acceptance(AttemptState.DISPATCHED, **{**common, "state_matches": False})
        is AttemptAcceptance.REJECTED_STALE_STATE
    )
    assert (
        attempt_acceptance(AttemptState.DISPATCHED, **{**common, "not_superseded": False})
        is AttemptAcceptance.REJECTED_SUPERSEDED
    )


# --- 2.8 Gate single-use consumption ----------------------------------------


@pytest.mark.unit
def test_gate_can_respond_only_when_open() -> None:
    from agents_orchestration.domain.state_machine import can_respond_gate

    assert can_respond_gate(GateState.OPEN)
    assert not can_respond_gate(GateState.RESPONDED)
    assert not can_respond_gate(GateState.CONSUMED)


@pytest.mark.unit
def test_gate_consume_requires_responded_and_is_single_use() -> None:
    assert can_consume_gate(GateState.RESPONDED)
    assert not can_consume_gate(GateState.OPEN)
    assert not can_consume_gate(GateState.CONSUMED)
    assert not can_consume_gate(GateState.EXPIRED)


@pytest.mark.unit
def test_gate_assert_consume_raises_on_single_use_violation() -> None:
    with pytest.raises(StateTransitionError):
        assert_gate_consume(GateState.CONSUMED)
    with pytest.raises(StateTransitionError):
        assert_gate_respond(GateState.CONSUMED)

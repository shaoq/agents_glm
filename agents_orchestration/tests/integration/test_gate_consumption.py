"""Integration tests for atomic Gate consumption via ``respond_gate`` (Section 3)."""

from __future__ import annotations

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import build_gate_continuation
from agents_orchestration.domain.enums import (
    EffectType,
    GateState,
    GateType,
    RunState,
    TerminationReason,
)
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.lifecycle import GateContinuation
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.gates import (
    GateContinuationError,
    GateNotOpenError,
    GateService,
)


def _seed_run(backend, clock, *, state=RunState.PLANNING, plan_version=1) -> Run:
    now = clock.now()
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=state,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=plan_version,
        created_at=now,
        updated_at=now,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    return run


def _open_gate(backend, clock, run, gate_type):
    with backend.unit_of_work() as uow:
        gate = GateService(uow, clock, backend.idgen).open(
            run,
            gate_type,
            actor="approver",
            role="approver",
            scope="plan",
            continuation=build_gate_continuation(gate_type, run),
        )
        uow.commit()
    return gate


def _effects(backend, run_id) -> list:
    with backend.unit_of_work() as uow:
        effects = [e.effect for e in uow.events.stream(run_id)]
        uow.rollback()
    return effects


# --- task 3.1-3.4: normal atomic consumption --------------------------------


@pytest.mark.integration
def test_respond_gate_applied_transition_consumes_run(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.PLAN_APPROVAL)
    service = OrchestrationService(backend)

    consumed = service.respond_gate(
        gate.gate_id,
        request_id="rq1",
        actor="approver",
        role="approver",
        payload={"outcome": "approved"},
    )
    assert consumed.state is GateState.CONSUMED
    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.RESEARCHING
        assert final.state_version == run.state_version + 1
        uow.rollback()
    effects = _effects(backend, run.run_id)
    assert EffectType.GATE_RESPONDED in effects
    assert EffectType.GATE_CONSUMED in effects
    assert EffectType.RUN_RESUMED in effects
    assert EffectType.RUN_STATE_TRANSITION in effects


@pytest.mark.integration
def test_respond_gate_same_state_clarifies_and_bumps_version(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.NORMALIZING, plan_version=None)
    gate = _open_gate(backend, fake_clock, run, GateType.GOAL_CLARIFICATION)
    service = OrchestrationService(backend)

    service.respond_gate(
        gate.gate_id,
        request_id="rq1",
        actor="approver",
        role="approver",
        payload={"outcome": "clarified", "clarification": "focus on pricing"},
    )
    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.NORMALIZING  # same-state resume
        assert final.state_version == run.state_version + 1
        assert final.goal_clarification == "focus on pricing"
        assert final.raw_goal == "g"  # raw_goal is never overwritten (task 4.2 / 2.2)
        uow.rollback()
    effects = _effects(backend, run.run_id)
    assert EffectType.RUN_RESUMED in effects
    assert EffectType.RUN_STATE_TRANSITION not in effects  # same-state: no transition event


@pytest.mark.integration
def test_respond_gate_cancelled_terminates_run(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.NORMALIZING, plan_version=None)
    gate = _open_gate(backend, fake_clock, run, GateType.GOAL_CLARIFICATION)
    service = OrchestrationService(backend)

    service.respond_gate(
        gate.gate_id,
        request_id="rq1",
        actor="approver",
        role="approver",
        payload={"outcome": "cancelled"},
    )
    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.CANCELED
        assert final.termination is TerminationReason.CANCELED
        uow.rollback()
    assert EffectType.RUN_TERMINATED in _effects(backend, run.run_id)


@pytest.mark.integration
def test_respond_gate_escalated_fails_run(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.REVIEWING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.CONFLICT_RESOLUTION)
    service = OrchestrationService(backend)

    service.respond_gate(
        gate.gate_id,
        request_id="rq1",
        actor="approver",
        role="approver",
        payload={"outcome": "escalated", "reason": "stuck"},
    )
    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.FAILED
        assert final.termination is TerminationReason.FAILED
        uow.rollback()


@pytest.mark.integration
def test_respond_gate_resolved_returns_reviewing_run_to_research(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.REVIEWING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.CONFLICT_RESOLUTION)

    OrchestrationService(backend).respond_gate(
        gate.gate_id,
        request_id="rq-resolved",
        actor="approver",
        role="approver",
        payload={"outcome": "resolved", "resolution": "collect missing source"},
    )

    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.RESEARCHING
        assert uow.gates.get(gate.gate_id).state is GateState.CONSUMED
        uow.rollback()


@pytest.mark.integration
def test_successful_gate_events_are_visible_after_open_version(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.NORMALIZING, plan_version=None)
    gate = _open_gate(backend, fake_clock, run, GateType.GOAL_CLARIFICATION)

    OrchestrationService(backend).respond_gate(
        gate.gate_id,
        request_id="rq-event-cursor",
        actor="approver",
        role="approver",
        payload={"outcome": "clarified", "clarification": "focus"},
    )

    with backend.unit_of_work() as uow:
        effects = {
            event.effect
            for event in uow.events.stream(
                run.run_id, after_state_version=gate.state_version
            )
        }
        uow.rollback()
    assert EffectType.GATE_RESPONDED in effects
    assert EffectType.GATE_CONSUMED in effects


# --- task 3.3: invalidation branch -----------------------------------------


@pytest.mark.integration
def test_respond_gate_invalidates_when_continuation_missing(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    with backend.unit_of_work() as uow:  # open WITHOUT a persisted continuation
        gate = GateService(uow, fake_clock, backend.idgen).open(
            run, GateType.PLAN_APPROVAL, actor="approver", role="approver", scope="plan"
        )
        uow.commit()
    service = OrchestrationService(backend)

    with pytest.raises(GateContinuationError):
        service.respond_gate(
            gate.gate_id,
            request_id="rq1",
            actor="approver",
            role="approver",
            payload={"outcome": "approved"},
        )
    with backend.unit_of_work() as uow:
        assert uow.gates.get(gate.gate_id).state is GateState.CANCELED
        assert uow.runs.get(run.run_id).state is RunState.PLANNING  # run unchanged
        uow.rollback()
    effects = _effects(backend, run.run_id)
    assert EffectType.GATE_INVALIDATED in effects
    assert EffectType.GATE_CONSUMED not in effects


@pytest.mark.integration
def test_respond_gate_invalidates_malformed_continuation_target(
    backend, fake_clock
) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    continuation = GateContinuation(
        origin_phase="plan",
        bound_state_version=run.state_version,
        bound_plan_version=run.current_plan_version,
        next_state_by_outcome=(("approved", "corrupt-state"),),
    )
    with backend.unit_of_work() as uow:
        gate = GateService(uow, fake_clock, backend.idgen).open(
            run,
            GateType.PLAN_APPROVAL,
            actor="approver",
            role="approver",
            scope="plan",
            continuation=continuation,
        )
        uow.commit()

    with pytest.raises(GateContinuationError, match="invalid_transition"):
        OrchestrationService(backend).respond_gate(
            gate.gate_id,
            request_id="rq-corrupt-target",
            actor="approver",
            role="approver",
            payload={"outcome": "approved"},
        )

    with backend.unit_of_work() as uow:
        assert uow.gates.get(gate.gate_id).state is GateState.CANCELED
        assert uow.runs.get(run.run_id) == run
        uow.rollback()
    assert EffectType.GATE_INVALIDATED in _effects(backend, run.run_id)


@pytest.mark.integration
def test_respond_gate_invalidates_on_stale_state_version(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.PLAN_APPROVAL)
    with backend.unit_of_work() as uow:  # drift the state version after binding
        drifted = uow.runs.get(run.run_id).transition(
            RunState.AWAITING_PLAN_APPROVAL, fake_clock.now()
        )
        uow.runs.save(drifted, expected_version=run.state_version)
        uow.commit()
    service = OrchestrationService(backend)

    with pytest.raises(GateContinuationError):
        service.respond_gate(
            gate.gate_id,
            request_id="rq1",
            actor="approver",
            role="approver",
            payload={"outcome": "approved"},
        )
    with backend.unit_of_work() as uow:
        assert uow.gates.get(gate.gate_id).state is GateState.CANCELED
        uow.rollback()


@pytest.mark.integration
def test_invalidated_gate_event_is_visible_after_stale_gate_version(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.PLAN_APPROVAL)
    with backend.unit_of_work() as uow:
        drifted = uow.runs.get(run.run_id).transition(
            RunState.AWAITING_PLAN_APPROVAL, fake_clock.now()
        )
        uow.runs.save(drifted, expected_version=run.state_version)
        uow.commit()

    with pytest.raises(GateContinuationError):
        OrchestrationService(backend).respond_gate(
            gate.gate_id,
            request_id="rq-invalidated-cursor",
            actor="approver",
            role="approver",
            payload={"outcome": "approved"},
        )

    with backend.unit_of_work() as uow:
        events = list(
            uow.events.stream(run.run_id, after_state_version=gate.state_version)
        )
        uow.rollback()
    assert any(event.effect is EffectType.GATE_INVALIDATED for event in events)


# --- task 3.5: CAS conflict / mid-flight exception rolls back ---------------


@pytest.mark.integration
def test_respond_gate_cas_conflict_rolls_back_all_side_effects(
    backend, fake_clock, monkeypatch
) -> None:
    from agents_orchestration.runtime.persistence.repositories import SqliteRunRepository
    from agents_orchestration.runtime.ports import StaleVersionError

    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.PLAN_APPROVAL)
    service = OrchestrationService(backend)

    def boom(self, run_arg, expected_version):  # noqa: ARG001
        raise StaleVersionError("simulated CAS conflict")

    monkeypatch.setattr(SqliteRunRepository, "save", boom)

    with pytest.raises(StaleVersionError):
        service.respond_gate(
            gate.gate_id,
            request_id="rq1",
            actor="approver",
            role="approver",
            payload={"outcome": "approved"},
        )
    with backend.unit_of_work() as uow:  # transaction rolled back
        assert uow.gates.get(gate.gate_id).state is GateState.OPEN
        assert uow.runs.get(run.run_id).state is RunState.PLANNING
        uow.rollback()
    effects = _effects(backend, run.run_id)
    assert EffectType.GATE_CONSUMED not in effects
    assert EffectType.RUN_RESUMED not in effects


# --- task 3.6: at-most-once / single-use -----------------------------------


@pytest.mark.integration
def test_respond_gate_is_single_use(backend, fake_clock) -> None:
    run = _seed_run(backend, fake_clock, state=RunState.PLANNING, plan_version=1)
    gate = _open_gate(backend, fake_clock, run, GateType.PLAN_APPROVAL)
    service = OrchestrationService(backend)

    service.respond_gate(
        gate.gate_id,
        request_id="rq1",
        actor="approver",
        role="approver",
        payload={"outcome": "approved"},
    )
    with pytest.raises(GateNotOpenError):  # second response rejected (single-use)
        service.respond_gate(
            gate.gate_id,
            request_id="rq2",
            actor="approver",
            role="approver",
            payload={"outcome": "approved"},
        )

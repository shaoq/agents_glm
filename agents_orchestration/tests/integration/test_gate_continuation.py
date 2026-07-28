"""Tests for Gate continuation build/apply and persistence (Ch.8 tasks 8.1-8.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.domain.coordination import (
    GATE_CONTINUATION_NEXT,
    apply_gate_continuation,
    build_gate_continuation,
)
from agents_orchestration.domain.enums import GateType, RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.lifecycle import Gate, GateContinuation
from agents_orchestration.domain.policy import RunPolicy, SystemLimits

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _run(state: RunState, *, state_version: int = 1, plan_version: int | None = None) -> Run:
    return Run(
        run_id="run-1",
        raw_goal="g",
        state=state,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=plan_version,
        state_version=state_version,
        created_at=NOW,
        updated_at=NOW,
    )


def _gate(run: Run, gate_type: GateType, cont: GateContinuation) -> Gate:
    return Gate(
        gate_id="g1",
        run_id=run.run_id,
        gate_type=gate_type,
        actor="system",
        role="orchestrator",
        scope=run.run_id,
        state_version=run.state_version,
        plan_version=run.current_plan_version,
        allowed_response_schema="{}",
        expires_at=NOW + timedelta(seconds=3600),
        continuation=cont,
    )


# --- Task 8.1 / 8.6: continuation model + 4 gate types ---------------------


@pytest.mark.unit
def test_gate_continuation_mapping_covers_four_gate_types() -> None:
    assert set(GATE_CONTINUATION_NEXT) == set(GateType)


@pytest.mark.unit
def test_build_plan_approval_continuation_binds_versions() -> None:
    run = _run(RunState.PLANNING, state_version=3, plan_version=2)
    cont = build_gate_continuation(GateType.PLAN_APPROVAL, run)
    assert cont.origin_phase == "plan"
    assert cont.bound_state_version == 3
    assert cont.bound_plan_version == 2
    assert cont.next_state_for("approved") == "researching"
    assert cont.next_state_for("rejected") == "planning"


# --- Task 8.5: deterministic continuation application ----------------------


@pytest.mark.unit
def test_apply_known_outcome_transitions_run() -> None:
    run = _run(RunState.PLANNING)
    gate = _gate(run, GateType.PLAN_APPROVAL, build_gate_continuation(GateType.PLAN_APPROVAL, run))
    moved = apply_gate_continuation(gate, run, "approved", NOW)
    assert moved.state is RunState.RESEARCHING
    assert moved.state_version == run.state_version + 1


# --- Task 8.4: stale / unknown outcomes do not advance ---------------------


@pytest.mark.unit
def test_apply_stale_binding_does_not_advance() -> None:
    bound = _run(RunState.PLANNING, state_version=1)
    gate = _gate(
        bound, GateType.PLAN_APPROVAL, build_gate_continuation(GateType.PLAN_APPROVAL, bound)
    )
    drifted = _run(RunState.PLANNING, state_version=2)  # state version drifted
    moved = apply_gate_continuation(gate, drifted, "approved", NOW)
    assert moved is drifted  # no transition applied


@pytest.mark.unit
def test_apply_unknown_outcome_does_not_advance() -> None:
    run = _run(RunState.PLANNING)
    gate = _gate(run, GateType.PLAN_APPROVAL, build_gate_continuation(GateType.PLAN_APPROVAL, run))
    moved = apply_gate_continuation(gate, run, "bogus", NOW)
    assert moved is run  # task 8.7: caller cannot choose an arbitrary target


# --- Task 8.2 / 8.3: coordinator opens a Gate with persisted continuation ---


@pytest.mark.integration
async def test_coordinator_open_gate_persists_continuation(backend) -> None:
    from agents_orchestration.domain.coordination import PhaseId
    from agents_orchestration.domain.goal import CompletionContract, GoalSpec
    from agents_orchestration.orchestration.coordinator import RunCoordinator
    from agents_orchestration.orchestration.phases import GoalPhaseHandler
    from agents_orchestration.orchestration.proposals import (
        GoalClarificationProposal,
        GoalNormalizationOutcome,
    )

    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.NORMALIZING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()

    clarification = GoalClarificationProposal(
        run_id=run.run_id, ambiguities=("missing objective",), questions=("Clarify?",)
    )

    class _Ambiguous:
        async def normalize(self, raw_goal, run_id):
            return GoalNormalizationOutcome(
                GoalSpec(raw_input="g", objective="", deliverables=()),
                CompletionContract(),
                clarification,
            )

    coord = RunCoordinator(backend, {PhaseId.GOAL: GoalPhaseHandler(_Ambiguous(), backend.idgen)})

    from agents_orchestration.domain.coordination import AdvanceDisposition

    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.BLOCKED

    with backend.unit_of_work() as uow:
        gates = list(uow.gates.open_for_run(run.run_id))
        assert len(gates) == 1
        gate = gates[0]
        assert gate.continuation is not None  # task 8.2: persisted with the Gate
        assert gate.continuation.origin_phase == "goal"
        uow.commit()

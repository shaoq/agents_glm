"""Integration tests for the RunCoordinator dispatcher (Ch.4 tasks 4.1-4.10)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    InputFingerprint,
    PhaseId,
    StageStatus,
)
from agents_orchestration.domain.enums import RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.orchestration.coordinator import (
    CoordinatorDiagnostics,
    PhaseContext,
    PhaseOutcome,
    RunCoordinator,
    transition_or_stay,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class _FakeHandler:
    """Deterministic phase handler that returns a canned outcome."""

    def __init__(self, phase: PhaseId, outcome: PhaseOutcome) -> None:
        self.phase = phase
        self._outcome = outcome
        self.calls = 0

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        self.calls += 1
        return self._outcome

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now) -> Run:
        moved = transition_or_stay(run, outcome.next_state, now)
        if moved.state is not run.state:
            uow.runs.save(moved, expected_version=run.state_version)
        return moved


def _seed(
    backend,
    state: RunState = RunState.NORMALIZING,
    *,
    plan_version: int | None = None,
    budget: Budget | None = None,
) -> Run:
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=state,
        policy=RunPolicy.from_limits(SystemLimits()),
        budget=budget or Budget(),
        current_plan_version=plan_version,
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    return run


# --- Task 4.1: CREATED -> NORMALIZING as one bounded advance ---------------


@pytest.mark.integration
async def test_created_advances_to_normalizing(backend) -> None:
    coord = RunCoordinator(backend, {})
    run = _seed(backend, RunState.CREATED)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.from_state is RunState.CREATED
    assert report.to_state is RunState.NORMALIZING


# --- Task 4.3: terminal / paused / open-gate short-circuit -----------------


@pytest.mark.integration
async def test_terminal_run_reports_terminal_without_work(backend) -> None:
    coord = RunCoordinator(
        backend,
        {
            PhaseId.GOAL: _FakeHandler(
                PhaseId.GOAL,
                PhaseOutcome(AdvanceDisposition.PROGRESSED, next_state=RunState.PLANNING),
            )
        },
    )
    run = _seed(backend, RunState.SUCCEEDED)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL


@pytest.mark.integration
async def test_paused_run_reports_idle(backend) -> None:
    coord = RunCoordinator(backend, {})
    run = _seed(backend, RunState.PAUSED)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "non-active" in report.reason


# --- Task 4.4: one advance calls a phase handler at most once --------------


@pytest.mark.integration
async def test_one_advance_calls_handler_at_most_once(backend) -> None:
    goal = _FakeHandler(
        PhaseId.GOAL,
        PhaseOutcome(AdvanceDisposition.PROGRESSED, next_state=RunState.PLANNING),
    )
    coord = RunCoordinator(backend, {PhaseId.GOAL: goal})
    run = _seed(backend, RunState.NORMALIZING)
    await coord.advance(run.run_id)
    assert goal.calls == 1
    # The Run advanced exactly one phase (NORMALIZING -> PLANNING), not further.
    with backend.unit_of_work() as uow:
        assert uow.runs.get(run.run_id).state is RunState.PLANNING
        uow.commit()


# --- Task 4.5: provider call happens outside a write transaction -----------


@pytest.mark.integration
async def test_handler_executes_outside_write_transaction(backend) -> None:
    seen: dict[str, object] = {}

    class _Probe:
        phase = PhaseId.GOAL

        async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
            seen["in_txn"] = backend.conn.in_transaction
            return PhaseOutcome(AdvanceDisposition.PROGRESSED, next_state=RunState.PLANNING)

        def accept(self, outcome: PhaseOutcome, run: Run, uow, now) -> Run:
            moved = transition_or_stay(run, outcome.next_state, now)
            if moved.state is not run.state:
                uow.runs.save(moved, expected_version=run.state_version)
            return moved

    coord = RunCoordinator(backend, {PhaseId.GOAL: _Probe()})
    run = _seed(backend, RunState.NORMALIZING)
    await coord.advance(run.run_id)
    assert seen["in_txn"] is False


# --- Task 4.6: stale phase result becomes an observation -------------------


@pytest.mark.integration
async def test_stale_result_does_not_advance_run(backend) -> None:
    class _Drift:
        phase = PhaseId.GOAL

        async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
            # Simulate a concurrent transition during the external call.
            with backend.unit_of_work() as uow:
                r = uow.runs.get(ctx.run.run_id)
                moved = r.transition(RunState.PLANNING, backend.clock.now())
                uow.runs.save(moved, expected_version=r.state_version)
                uow.commit()
            return PhaseOutcome(
                AdvanceDisposition.PROGRESSED, next_state=RunState.PLANNING, reason="goal"
            )

        def accept(self, outcome: PhaseOutcome, run: Run, uow, now) -> Run:
            return run  # not reached: the drifted result is classified stale

    coord = RunCoordinator(backend, {PhaseId.GOAL: _Drift()})
    run = _seed(backend, RunState.NORMALIZING)
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.IDLE
    assert "stale" in report.reason
    # Run stays at the drifted state; coordinator did not advance again.
    with backend.unit_of_work() as uow:
        assert uow.runs.get(run.run_id).state is RunState.PLANNING
        uow.commit()


# --- Task 4.7: stage Event + semantic Checkpoint in the accept transaction -


@pytest.mark.integration
async def test_progressed_persists_stage_event_and_checkpoint(backend) -> None:
    fp = InputFingerprint(state_version=1)
    handler = _FakeHandler(
        PhaseId.GOAL,
        PhaseOutcome(
            AdvanceDisposition.PROGRESSED,
            next_state=RunState.PLANNING,
            input_fingerprint=fp,
            stage_logical_key="goal",
            reason="normalized",
        ),
    )
    coord = RunCoordinator(backend, {PhaseId.GOAL: handler})
    run = _seed(backend, RunState.NORMALIZING)
    await coord.advance(run.run_id)

    with backend.unit_of_work() as uow:
        stages = uow.stages.for_logical_stage(run.run_id, "goal")
        assert any(s.status is StageStatus.ACCEPTED for s in stages)
        assert uow.checkpoints.latest(run.run_id) is not None
        uow.commit()


# --- Task 4.8: IDLE vs BLOCKED --------------------------------------------


@pytest.mark.integration
async def test_idle_and_blocked_dispositions_are_distinct(backend) -> None:
    idle = _FakeHandler(PhaseId.GOAL, PhaseOutcome(AdvanceDisposition.IDLE))
    coord = RunCoordinator(backend, {PhaseId.GOAL: idle})
    run = _seed(backend, RunState.NORMALIZING)
    assert (await coord.advance(run.run_id)).disposition is AdvanceDisposition.IDLE

    blocked = _FakeHandler(
        PhaseId.GOAL, PhaseOutcome(AdvanceDisposition.BLOCKED, reason="ambiguous")
    )
    coord2 = RunCoordinator(backend, {PhaseId.GOAL: blocked})
    run2 = _seed(backend, RunState.NORMALIZING)
    report = await coord2.advance(run2.run_id)
    assert report.disposition is AdvanceDisposition.BLOCKED
    assert report.reason == "ambiguous"


# --- Task 4.9: deadline / budget guards terminate --------------------------


@pytest.mark.integration
async def test_budget_deadline_exhausted_terminates_failed(backend) -> None:
    expired = Budget.from_deadline(datetime(2020, 1, 1, tzinfo=UTC))
    run = _seed(backend, RunState.NORMALIZING, budget=expired)
    coord = RunCoordinator(backend, {})
    report = await coord.advance(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL
    assert report.to_state is RunState.FAILED


# --- Task 4.10: structured, redacted diagnostics ---------------------------


@pytest.mark.integration
def test_diagnostics_to_dict_redacts_to_codes() -> None:
    d = CoordinatorDiagnostics(
        code="policy_violation",
        message="replan budget exhausted",
        run_id="run-1",
        phase=PhaseId.REVIEW,
        stage_logical_key="review:pass1",
    )
    out = d.to_dict()
    assert out["code"] == "policy_violation"
    assert out["phase"] == "review"
    assert out["stage_logical_key"] == "review:pass1"

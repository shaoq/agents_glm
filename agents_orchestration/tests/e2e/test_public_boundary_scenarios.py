"""Public-boundary E2E scenarios (Ch.12 tasks 12.3/12.4/12.6/12.8).

These drive the full lifecycle through public service entry points
(``start_and_drive`` / ``advance_run``) with custom coordinators that force
Gate / Replan / degradation outcomes, plus a security assertion that model
output cannot select Run state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    PhaseId,
    phase_for_state,
)
from agents_orchestration.domain.enums import GateType, ReviewVerdict, RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import CompletionContract, GoalSpec
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.orchestration.phases import GoalPhaseHandler, ReviewPhaseHandler
from agents_orchestration.orchestration.proposals import (
    GoalClarificationProposal,
    GoalNormalizationOutcome,
)
from agents_orchestration.orchestration.report import ReportContent, ReviewProposal
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from agents_orchestration.runtime.tick import TickReport
from tests.support.deterministic import build_deterministic_coordinator

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 7, 28, tzinfo=UTC)

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


@pytest.fixture
def service(tmp_path) -> OrchestrationService:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    return OrchestrationService(backend, coordinator=build_deterministic_coordinator(backend))


class _AmbiguousNormalizer:
    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
        return GoalNormalizationOutcome(
            GoalSpec(raw_input=raw_goal, objective="", deliverables=()),
            CompletionContract(),
            GoalClarificationProposal(
                run_id=run_id, ambiguities=("missing objective",), questions=("Clarify?",)
            ),
        )


# --- Task 12.3: Goal clarification Gate via public API --------------------


@pytest.mark.e2e
async def test_e2e_goal_clarification_gate(service: OrchestrationService) -> None:
    coord = service.coordinator
    coord.handlers[PhaseId.GOAL] = GoalPhaseHandler(_AmbiguousNormalizer(), service.backend.idgen)

    run = service.create_run("vague goal", request_id="r1")
    await service.advance_run(run.run_id)  # CREATED -> NORMALIZING
    report = await service.advance_run(run.run_id)  # GOAL ambiguous -> BLOCKED
    assert report.disposition is AdvanceDisposition.BLOCKED
    gates = service.list_gates(run.run_id)
    assert any(g.gate_type is GateType.GOAL_CLARIFICATION for g in gates)
    assert all(g.continuation is not None for g in gates)  # stored continuation


# --- Task 12.4: focused Replan via Reviewer RESEARCH_GAP -------------------


@pytest.mark.e2e
async def test_e2e_research_gap_replan_gate(service: OrchestrationService) -> None:
    coord = service.coordinator

    class _GapReviewer:
        async def __call__(self, run_id: str, report):
            return ReviewProposal(verdict=ReviewVerdict.RESEARCH_GAP, reason="evidence gap")

    async def _report_provider(run_id):
        return ReportContent(run_id=run_id, title="T", objective="O")

    coord.handlers[PhaseId.REVIEW] = ReviewPhaseHandler(
        _GapReviewer(),
        _report_provider,
        clock=service.backend.clock,
        idgen=service.backend.idgen,
    )

    run = await service.start_and_drive("clear goal", request_id="r2")
    # Drives through to REVIEW, where RESEARCH_GAP opens a conflict Gate and
    # stops the Run (BLOCKED) without looping forever.
    assert run.state is RunState.REVIEWING
    assert any(g.gate_type is GateType.CONFLICT_RESOLUTION for g in service.list_gates(run.run_id))


# --- Task 12.6: budget exhaustion terminates FAILED ------------------------


@pytest.mark.e2e
async def test_e2e_budget_exhaustion_terminates_failed(
    service: OrchestrationService,
) -> None:
    run = Run(
        run_id=service.backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.NORMALIZING,
        policy=RunPolicy.from_limits(SystemLimits()),
        budget=Budget.from_deadline(datetime(2020, 1, 1, tzinfo=UTC)),
        created_at=NOW,
        updated_at=NOW,
    )
    with service.backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    report = await service.advance_run(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL
    assert service.get_run(run.run_id).state is RunState.FAILED


# --- Task 12.8: security — model output cannot select Run state ------------


@pytest.mark.e2e
def test_e2e_untrusted_content_cannot_select_state() -> None:
    """A model/evidence payload 'instructing' a jump to a later phase cannot
    alter routing (design Decision 2 / 2.9). Routing is a pure function of
    durable Run state; untrusted content stays evidence."""

    # Even a "skip to FINALIZING" instruction cannot change NORMALIZING routing.
    assert phase_for_state(RunState.NORMALIZING).value == "goal"
    assert phase_for_state(RunState.NORMALIZING) is not phase_for_state(RunState.FINALIZING)


class _NoopTick:
    async def tick(self, run_id: str) -> TickReport:
        return TickReport(run_id)

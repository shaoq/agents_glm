"""Integration tests for the deterministic test-double composition (Ch.9)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.coordination import AdvanceDisposition
from agents_orchestration.domain.enums import RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from tests.support.deterministic import build_deterministic_coordinator

NOW = datetime(2026, 7, 28, tzinfo=UTC)


@pytest.mark.integration
async def test_deterministic_coordinator_wires_all_phases(backend) -> None:
    coord = build_deterministic_coordinator(backend)
    # Every phase has a handler wired.
    from agents_orchestration.domain.coordination import PhaseId

    for phase in (
        PhaseId.GOAL,
        PhaseId.PLAN,
        PhaseId.RESEARCH,
        PhaseId.ANALYZE,
        PhaseId.WRITE,
        PhaseId.REVIEW,
        PhaseId.FINALIZE,
    ):
        assert phase in coord.handlers


@pytest.mark.integration
async def test_deterministic_composition_drives_clear_goal_to_succeeded(backend) -> None:
    coord = build_deterministic_coordinator(backend)
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="analyze X clearly",
        state=RunState.NORMALIZING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()

    blocked = False
    for _ in range(80):
        report = await coord.advance(run.run_id)
        if report.disposition is AdvanceDisposition.TERMINAL:
            break
        if report.disposition is AdvanceDisposition.BLOCKED:
            blocked = True
            break

    assert not blocked, "happy path should not block"
    with backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.SUCCEEDED
        # report.md / report.json / run-summary.json plus the ANALYZE-accepted
        # analysis artifact now materialized by the handoff (task 3.3).
        kinds = {a.kind for a in uow.artifacts.list_all()}
        assert {
            "report_markdown",
            "report_json",
            "run_summary",
            "analysis",
        } <= kinds
        uow.commit()


@pytest.mark.integration
async def test_settings_composition_preserves_required_research_for_empty_evidence(
    backend,
) -> None:
    """The production ANALYZE provider must reproduce ResearchPhaseHandler's
    required Join semantics so zero evidence reaches the deterministic L0 path."""

    from agents_orchestration.config import Settings
    from agents_orchestration.domain.coordination import PhaseId
    from agents_orchestration.domain.enums import Sufficiency
    from agents_orchestration.orchestration.composition import (
        build_production_coordinator_from_settings,
    )

    coordinator = build_production_coordinator_from_settings(backend, Settings())
    evidence = await coordinator.handlers[PhaseId.ANALYZE].evidence_provider("run-empty")

    assert evidence.sufficiency is Sufficiency.INSUFFICIENT
    assert evidence.missing_required


@pytest.mark.integration
def test_settings_composition_wires_research_reasoning_reservation(backend) -> None:
    from agents_orchestration.config import Settings
    from agents_orchestration.domain.coordination import PhaseId
    from agents_orchestration.orchestration.composition import (
        build_production_coordinator_from_settings,
    )

    coordinator = build_production_coordinator_from_settings(
        backend,
        Settings(research_reasoning_reservation_tokens=321),
    )

    assert coordinator.handlers[PhaseId.RESEARCH].tick.reasoning_reservation_tokens == 321


@pytest.mark.integration
def test_production_composition_rejects_incomplete(backend) -> None:
    """Task 9.8: a production profile with a missing required port must fail
    loudly rather than silently substituting a Fake."""

    from agents_orchestration.orchestration.composition import (
        CompositionError,
        build_production_coordinator,
    )

    with pytest.raises(CompositionError, match="missing"):
        build_production_coordinator(
            backend,
            executor=None,
            normalizer=None,
            planner=None,
            analyst=None,
            writer=None,
            reviewer=None,
            research_evidence=None,
            evidence_set=None,
            analysis_provider=None,
            report_provider=None,
            deliverables_provider=None,
            allowed_capabilities=frozenset(),
        )

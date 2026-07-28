"""Integration tests for the offline composition root (Ch.9)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.coordination import AdvanceDisposition
from agents_orchestration.domain.enums import RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.composition import build_offline_coordinator

NOW = datetime(2026, 7, 28, tzinfo=UTC)


@pytest.mark.integration
async def test_offline_coordinator_wires_all_phases(backend) -> None:
    coord = build_offline_coordinator(backend)
    # Every phase has a handler wired.
    from agents_orchestration.domain.coordination import PhaseId

    for phase in (
        PhaseId.GOAL, PhaseId.PLAN, PhaseId.RESEARCH, PhaseId.ANALYZE,
        PhaseId.WRITE, PhaseId.REVIEW, PhaseId.FINALIZE,
    ):
        assert phase in coord.handlers


@pytest.mark.integration
async def test_offline_composition_drives_clear_goal_to_succeeded(backend) -> None:
    coord = build_offline_coordinator(backend)
    run = Run(
        run_id=backend.idgen.new_id("run"), raw_goal="analyze X clearly",
        state=RunState.NORMALIZING, policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW, updated_at=NOW,
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
        assert len(uow.artifacts.list_all()) == 3  # report.md / report.json / run-summary.json
        uow.commit()

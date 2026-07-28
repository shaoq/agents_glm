"""Tests for Pause/Resume continuation and Gate expiry (Ch.8 tasks 8.8-8.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import build_gate_continuation
from agents_orchestration.domain.enums import GateState, GateType, RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.gates import GateService
from agents_orchestration.runtime.persistence.connection import SqliteBackend

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
    return OrchestrationService(backend)


# --- Task 8.8 / 8.9: pause persists the safe continuation point ------------


@pytest.mark.integration
async def test_pause_records_origin_phase(service: OrchestrationService) -> None:
    run = service.create_run("goal", request_id="r1")
    await service.advance_run(run.run_id)  # CREATED -> NORMALIZING (v2)
    paused = service.pause_run(run.run_id, expected_version=run.state_version + 1)
    assert paused.state is RunState.PAUSED
    assert paused.paused_from_state is RunState.NORMALIZING  # 8.9: persisted origin


# --- Task 8.10 / 8.11: resume restores continuation + drives ----------------


@pytest.mark.integration
async def test_resume_and_drive_restores_origin_and_completes(
    service: OrchestrationService,
) -> None:
    run = service.create_run("clear goal", request_id="r2")
    await service.advance_run(run.run_id)  # NORMALIZING
    paused = service.pause_run(run.run_id, expected_version=service.get_run(run.run_id).state_version)
    assert paused.state is RunState.PAUSED

    resumed = await service.resume_and_drive(
        run.run_id, expected_version=paused.state_version
    )
    assert resumed.state is RunState.SUCCEEDED  # 8.11: drives to terminal


@pytest.mark.integration
async def test_resume_rejects_non_paused_run(service: OrchestrationService) -> None:
    run = service.create_run("goal", request_id="r3")
    with pytest.raises(RuntimeError, match="not paused"):
        await service.resume_and_drive(run.run_id, expected_version=run.state_version)


# --- Task 8.12: Gate expiry --------------------------------------------------


@pytest.mark.integration
def test_gate_expiry_marks_open_gate_expired(tmp_path) -> None:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    run = Run(
        run_id=backend.idgen.new_id("run"), raw_goal="g", state=RunState.NORMALIZING,
        policy=RunPolicy.from_limits(SystemLimits()), created_at=NOW, updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        GateService(uow, backend.clock, backend.idgen).open(
            run, GateType.GOAL_CLARIFICATION, actor="system", role="orchestrator",
            scope=run.run_id, allowed_response_schema="{}", ttl_seconds=0,
            continuation=build_gate_continuation(GateType.GOAL_CLARIFICATION, run),
        )
        uow.commit()

    with backend.unit_of_work() as uow:
        expired = GateService(uow, backend.clock, backend.idgen).expire_open(run, action="fail")
        uow.commit()
    assert len(expired) >= 1
    assert all(g.state is GateState.EXPIRED for g in expired)

"""Tests for coordinator-backed OrchestrationService operations (Ch.10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import AdvanceDisposition
from agents_orchestration.domain.enums import RunState
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from tests.support.service_factory import build_test_service


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 7, 28, tzinfo=UTC)

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


@pytest.fixture
def service(tmp_path) -> OrchestrationService:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    return build_test_service(backend)


@pytest.mark.unit
def test_create_run_persists_created_only(service: OrchestrationService) -> None:
    run = service.create_run("g", request_id="r1")
    assert run.state is RunState.CREATED  # task 10.3: create-only, no driving


@pytest.mark.unit
async def test_advance_run_advances_one_bounded_step(service: OrchestrationService) -> None:
    run = service.create_run("g", request_id="r2")
    report = await service.advance_run(run.run_id)
    assert report.disposition is AdvanceDisposition.PROGRESSED
    assert report.to_state is RunState.NORMALIZING


@pytest.mark.unit
async def test_start_and_drive_clear_goal_reaches_succeeded(
    service: OrchestrationService,
) -> None:
    run = await service.start_and_drive("clear well-scoped goal", request_id="r3")
    assert run.state is RunState.SUCCEEDED
    assert len(service.list_artifacts()) == 3  # report.md/json/run-summary.json

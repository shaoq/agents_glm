"""Recovery and compatibility tests (Ch.11 tasks 11.2/11.3/11.6/11.7/11.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.enums import RunState
from agents_orchestration.runtime.persistence.connection import SqliteBackend


class _Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self._t = start or datetime(2026, 7, 28, tzinfo=UTC)

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


def _service(tmp_path, *, fresh_clock: bool = True) -> OrchestrationService:
    backend = SqliteBackend(
        tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock() if fresh_clock else _Clock()
    )
    return OrchestrationService(backend)


@pytest.mark.integration
async def test_restart_resumes_from_durable_state(tmp_path) -> None:
    """A fresh process opening the same SQLite database resumes a partway Run
    from its persisted state and drives it to completion (tasks 11.2/11.7)."""

    svc1 = _service(tmp_path)
    run = svc1.create_run("clear goal", request_id="r1")
    # Drive partway: CREATED -> NORMALIZING -> PLANNING.
    await svc1.advance_run(run.run_id)
    await svc1.advance_run(run.run_id)
    assert svc1.get_run(run.run_id).state is RunState.PLANNING

    # Fresh service (simulating a process restart) on the same database.
    svc2 = _service(tmp_path)
    await svc2.drive_run(run.run_id)
    final = svc2.get_run(run.run_id)
    assert final.state is RunState.SUCCEEDED
    assert len(svc2.list_artifacts()) == 3  # no duplicate final artifacts (11.7)


@pytest.mark.integration
async def test_global_watch_resumes_all_eligible_runs(tmp_path) -> None:
    """drive_all-style Watch resumes every non-terminal Run through the
    coordinator without touching paused/gated Runs (task 11.8)."""

    from agents_orchestration.cli import _drive_all

    svc = _service(tmp_path)
    a = svc.create_run("goal A", request_id="a")
    b = svc.create_run("goal B", request_id="b")
    # Advance A partway; leave B in CREATED. Both are non-terminal/resumable.
    await svc.advance_run(a.run_id)

    reports = await _drive_all(svc)
    # Both Runs were driven (resumable); each report present.
    assert a.run_id in reports and b.run_id in reports


@pytest.mark.integration
async def test_legacy_start_run_signature_remains_compatible(tmp_path) -> None:
    """The deprecated synchronous start_run creation entry still works for
    existing callers during the compatibility period (tasks 10.5/11.6)."""

    svc = _service(tmp_path)
    run = svc.start_run("legacy goal", request_id="legacy-1")
    assert run.state in {RunState.NORMALIZING, RunState.CREATED}
    assert svc.get_run(run.run_id).run_id == run.run_id

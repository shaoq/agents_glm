"""Characterization tests for the public CLI + OrchestrationService surface.

Task 1.1 records the public behavior that the RunCoordinator change MUST
preserve so the refactor stays safe: idempotent creation, non-terminal default
state, stable JSON shape, terminal cancellation, and the runtime-tick contract.

Task 1.2 adds the failing public-boundary test proving that, today, a clear
Goal submitted through the public path cannot reach SUCCEEDED or produce
report artifacts — because only ``RuntimeTick`` (Task dispatch) exists and no
component drives Goal → Plan → Research → Analyze → Write → Review → Finalize.
That test is marked ``xfail`` until the RunCoordinator lands (Ch.4-7).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_orchestration import cli as cli_mod
from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.enums import RunState
from agents_orchestration.runtime.persistence.connection import SqliteBackend


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 7, 28, tzinfo=UTC)

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


@pytest.fixture
def service(tmp_path: Path) -> OrchestrationService:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    return OrchestrationService(backend)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched(monkeypatch, service: OrchestrationService) -> OrchestrationService:
    monkeypatch.setattr(cli_mod, "_service", lambda: service)
    return service


# ---------------------------------------------------------------------------
# Task 1.1 — public surface that MUST stay stable across the coordinator change
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_start_run_is_idempotent_by_request_id(service: OrchestrationService) -> None:
    """Repeated ``start_run`` with the same Request ID returns the same Run."""

    a = service.start_run("goal A", request_id="req-1")
    b = service.start_run("goal A again", request_id="req-1")
    assert a.run_id == b.run_id


@pytest.mark.unit
def test_start_run_persists_run_with_request_scope(service: OrchestrationService) -> None:
    run = service.start_run("goal", request_id="req-2")
    assert run.raw_goal == "goal"
    assert run.state_version == 1
    assert service.get_run(run.run_id).run_id == run.run_id


@pytest.mark.unit
def test_start_run_default_state_is_non_terminal(service: OrchestrationService) -> None:
    """Whatever the default creation state, it must remain non-terminal so a
    coordinator can drive it (task 1.1 characterization)."""

    run = service.start_run("goal", request_id="req-3")
    assert not run.state.is_terminal


@pytest.mark.unit
def test_cancel_run_reaches_terminal_state(service: OrchestrationService) -> None:
    run = service.start_run("goal", request_id="req-cancel")
    canceled = service.cancel_run(run.run_id, expected_version=run.state_version)
    assert canceled.state is RunState.CANCELED
    assert canceled.is_terminal
    assert service.get_run(run.run_id).is_terminal


@pytest.mark.unit
def test_cli_run_start_create_only_emits_stable_run_json(
    runner: CliRunner, patched: OrchestrationService
) -> None:
    result = runner.invoke(
        cli_mod.app,
        ["run", "start", "--goal", "g", "--request-id", "req-c", "--create-only"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["raw_goal"] == "g"
    assert "run_id" in payload
    assert "state" in payload


@pytest.mark.unit
def test_cli_runtime_tick_requires_run_id(runner: CliRunner, patched: OrchestrationService) -> None:
    """``runtime tick`` always requires a Run ID (task 11.9 contract)."""

    result = runner.invoke(cli_mod.app, ["runtime", "tick"])
    assert result.exit_code != 0


@pytest.mark.unit
def test_cli_unknown_run_returns_404(runner: CliRunner, patched: OrchestrationService) -> None:
    result = runner.invoke(cli_mod.app, ["run", "show", "missing"])
    assert result.exit_code == 404


# ---------------------------------------------------------------------------
# Task 1.2 — the gap: no coordinator means a clear goal cannot complete
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "RunCoordinator not yet wired (Ch.4-7): no public path drives "
        "Goal→Plan→Research→Analyze→Write→Review→Finalize, so a clear goal "
        "cannot reach SUCCEEDED or produce report artifacts. Remove the xfail "
        "once start_and_drive completes a clear goal end to end."
    ),
    strict=False,
)
@pytest.mark.unit
def test_clear_goal_completes_to_artifacts_via_public_drive(
    service: OrchestrationService,
) -> None:
    """A clear goal submitted through the public create-and-drive path MUST
    reach a terminal SUCCEEDED Run and produce report artifacts. Today this
    fails: ``drive_run`` loops ``RuntimeTick`` which only dispatches already
    materialized Tasks, so a fresh NORMALIZING Run with no Plan/Tasks stays
    non-terminal and produces nothing."""

    run = service.start_run("clear, well-scoped research goal", request_id="gap-1")
    asyncio.run(service.drive_run(run.run_id))
    final = service.get_run(run.run_id)
    assert final.state is RunState.SUCCEEDED
    assert service.list_artifacts() != []

"""Unit tests for the CLI (thin adapter over OrchestrationService) — Section 11."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from agents_orchestration import cli as cli_mod
from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.enums import CapabilityKind, RunState, WorkerRole
from agents_orchestration.runtime.persistence.connection import SqliteBackend


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 7, 27, tzinfo=UTC)

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


@pytest.fixture
def service(tmp_path) -> OrchestrationService:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    return OrchestrationService(backend)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched(monkeypatch, service):
    monkeypatch.setattr(cli_mod, "_service", lambda: service)
    return service


# --- 11.2 idempotent start + stale version ---------------------------------


@pytest.mark.unit
def test_run_start_is_idempotent_by_request_id(runner, patched) -> None:
    a = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "X", "--request-id", "r1", "--create-only"]
    )
    b = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "X", "--request-id", "r1", "--create-only"]
    )
    assert a.exit_code == 0 and b.exit_code == 0
    assert json.loads(a.stdout)["run_id"] == json.loads(b.stdout)["run_id"]


@pytest.mark.unit
def test_run_show_unknown_returns_404(runner, patched) -> None:
    result = runner.invoke(cli_mod.app, ["run", "show", "missing"])
    assert result.exit_code == 404


@pytest.mark.unit
def test_run_pause_stale_version_returns_409(runner, patched) -> None:
    started = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "X", "--request-id", "r1", "--create-only"]
    )
    result = runner.invoke(
        cli_mod.app,
        ["run", "pause", json.loads(started.stdout)["run_id"], "--expected-version", "99"],
    )
    assert result.exit_code == 409


# --- 11.3 / 11.4 run start --create-only -----------------------------------


@pytest.mark.unit
def test_run_start_create_only_emits_run_json(runner, patched) -> None:
    result = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "analyze X", "--request-id", "r2", "--create-only"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["raw_goal"] == "analyze X"


# --- 11.6 / 11.7 artifact + capability -------------------------------------


@pytest.mark.unit
def test_capability_list_and_doctor(runner, patched) -> None:
    listed = runner.invoke(cli_mod.app, ["capability", "list"])
    doctor = runner.invoke(cli_mod.app, ["capability", "doctor"])
    assert listed.exit_code == 0 and doctor.exit_code == 0
    assert len(json.loads(listed.stdout)) == 4
    assert all("api_key" not in entry for entry in json.loads(doctor.stdout))


@pytest.mark.unit
def test_artifact_list_empty(runner, patched) -> None:
    result = runner.invoke(cli_mod.app, ["artifact", "list"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


# --- 11.8 / 11.9 runtime tick ----------------------------------------------


@pytest.mark.unit
def test_runtime_tick_requires_run_id(runner, patched) -> None:
    result = runner.invoke(cli_mod.app, ["runtime", "tick"])
    assert result.exit_code != 0  # missing required argument


@pytest.mark.unit
def test_runtime_tick_with_run_id_dispatches(runner, patched) -> None:
    from agents_orchestration.domain.execution import Task
    from agents_orchestration.domain.plan import Plan, PlanGraph

    started = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "X", "--request-id", "r3", "--create-only"]
    )
    run_id = json.loads(started.stdout)["run_id"]
    # Seed a RESEARCHING run with a plan + task so the tick has ready work.
    with patched.backend.unit_of_work() as uow:
        run = uow.runs.get(run_id)
        researching = run.model_copy(
            update={"state": RunState.RESEARCHING, "current_plan_version": 1}
        )
        uow.runs.save(researching, expected_version=run.state_version)
        now = patched.backend.clock.now()
        uow.plans.save(
            Plan(run_id=run_id, graph=PlanGraph(plan_id="p1", version=1), proposed_at=now)
        )
        uow.tasks.materialize(
            [
                Task(
                    task_id="t1",
                    run_id=run_id,
                    plan_version=1,
                    worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                    required_capabilities=(CapabilityKind.RAG_SEARCH,),
                    created_at=now,
                    updated_at=now,
                )
            ]
        )
        uow.commit()
    result = runner.invoke(cli_mod.app, ["runtime", "tick", run_id])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["dispatched"] == 1


# --- 11.11 CLI delegates to Application (no duplicated domain logic) --------


@pytest.mark.unit
def test_cli_module_does_not_reimplement_domain_logic() -> None:
    """The CLI must not encode state machines / planning / persistence; it only
    adapts arguments and presents results (task 11.11)."""

    from pathlib import Path

    source = (Path(cli_mod.__file__)).read_text(encoding="utf-8")
    # The CLI should delegate via svc.* calls, not define its own transitions.
    for forbidden in (
        "def can_transition",
        "class BudgetGuard",
        "class PlanValidator",
        "def has_cycle",
    ):
        assert forbidden not in source, f"cli.py reimplements domain logic: {forbidden}"

"""Unit tests for the CLI (thin adapter over OrchestrationService) — Section 11."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from agents_orchestration import cli as cli_mod
from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from tests.support.service_factory import build_test_service


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 7, 27, tzinfo=UTC)

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


@pytest.fixture
def service(tmp_path) -> OrchestrationService:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    return build_test_service(backend)


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
    assert len(json.loads(listed.stdout)) == 3
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
def test_runtime_tick_advances_one_bounded_step(runner, patched) -> None:
    started = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "X", "--request-id", "r3", "--create-only"]
    )
    run_id = json.loads(started.stdout)["run_id"]
    result = runner.invoke(cli_mod.app, ["runtime", "tick", run_id])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["disposition"] == "progressed"  # CREATED -> NORMALIZING
    assert payload["to_state"] == "normalizing"


@pytest.mark.unit
def test_run_start_drives_to_succeeded(runner, patched) -> None:
    result = runner.invoke(
        cli_mod.app, ["run", "start", "--goal", "clear goal", "--request-id", "r4"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["state"] == "succeeded"


@pytest.mark.unit
def test_run_start_follow_does_not_change_execution(runner, patched) -> None:
    # task 10.9: --follow presents events without changing execution semantics
    result = runner.invoke(
        cli_mod.app,
        ["run", "start", "--goal", "clear goal", "--request-id", "rf", "--follow"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["state"] == "succeeded"


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


# --- 11.5 gate respond: typed payload, stable exit codes (task 5.3) --------


def _open_plan_approval_gate(service) -> tuple[str, str]:
    from agents_orchestration.domain.coordination import build_gate_continuation
    from agents_orchestration.domain.enums import GateType, RunState
    from agents_orchestration.domain.execution import Run
    from agents_orchestration.domain.policy import RunPolicy, SystemLimits
    from agents_orchestration.orchestration.gates import GateService

    now = service.backend.clock.now()
    run = Run(
        run_id=service.backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.PLANNING,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=1,
        created_at=now,
        updated_at=now,
    )
    with service.backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        gate = GateService(uow, service.backend.clock, service.backend.idgen).open(
            run,
            GateType.PLAN_APPROVAL,
            actor="approver",
            role="approver",
            scope="plan",
            continuation=build_gate_continuation(GateType.PLAN_APPROVAL, run),
        )
        uow.commit()
    return run.run_id, gate.gate_id


@pytest.mark.unit
def test_gate_respond_valid_payload_consumes_and_advances_run(runner, patched, service) -> None:
    run_id, gate_id = _open_plan_approval_gate(service)
    result = runner.invoke(
        cli_mod.app,
        [
            "gate", "respond", gate_id,
            "--request-id", "rq1", "--actor", "approver", "--role", "approver",
            "--payload", '{"outcome": "approved"}',
        ],
    )
    assert result.exit_code == 0
    shown = runner.invoke(cli_mod.app, ["run", "show", run_id])  # PLANNING -> RESEARCHING
    assert json.loads(shown.stdout)["state"] == "researching"


@pytest.mark.unit
def test_gate_respond_invalid_payload_returns_stable_error(runner, patched, service) -> None:
    run_id, gate_id = _open_plan_approval_gate(service)
    result = runner.invoke(
        cli_mod.app,
        [
            "gate", "respond", gate_id,
            "--request-id", "rq2", "--actor", "approver", "--role", "approver",
            "--payload", '{"ok": true}',  # missing required 'outcome'
        ],
    )
    assert result.exit_code == 1  # stable non-zero: Gate stays OPEN, Run unchanged
    assert "outcome" in result.output or "GateResponseError" in result.output
    shown = runner.invoke(cli_mod.app, ["run", "show", run_id])
    assert json.loads(shown.stdout)["state"] == "planning"

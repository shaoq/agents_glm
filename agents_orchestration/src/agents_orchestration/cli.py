"""Typer CLI: a thin adapter over :class:`OrchestrationService` (Section 11).

Commands: ``run start/show/watch/pause/resume/cancel``, ``gate list/respond``,
``artifact list/export``, ``capability list/doctor``, ``runtime tick/watch``.
The CLI performs argument adaptation and presentation only — all domain logic
lives in the Application service (task 11.11). Typed domain failures map to
stable exit codes with JSON diagnostics (task 11.10).
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from agents_orchestration import __version__
from agents_orchestration.application.service import DuplicateStartError, OrchestrationService
from agents_orchestration.config import load_settings
from agents_orchestration.runtime.ports import StaleVersionError


def _service() -> OrchestrationService:
    settings = load_settings()
    from agents_orchestration.runtime.persistence.connection import SqliteBackend

    backend = SqliteBackend(settings.sqlite_path, settings.artifact_dir)
    return OrchestrationService(backend, production=True)


def _exit_for(exc: Exception) -> int:
    if isinstance(exc, StaleVersionError | DuplicateStartError):
        return 409
    if isinstance(exc, KeyError):
        return 404
    return 1


def _diagnostic(exc: Exception) -> str:
    return json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False)


app = typer.Typer(name="agents-orchestration", help="Local-first durable research runtime.")
run_app = typer.Typer(help="Run lifecycle.")
gate_app = typer.Typer(help="Human gates.")
artifact_app = typer.Typer(help="Artifacts.")
capability_app = typer.Typer(help="Capabilities.")
runtime_app = typer.Typer(help="Durable runtime.")
app.add_typer(run_app, name="run")
app.add_typer(gate_app, name="gate")
app.add_typer(artifact_app, name="artifact")
app.add_typer(capability_app, name="capability")
app.add_typer(runtime_app, name="runtime")


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Show version and exit.", is_eager=True)
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit
    if ctx.invoked_subcommand is None:
        typer.echo(__version__)


@run_app.command("start")
def run_start(
    goal: Annotated[str, typer.Option("--goal")],
    request_id: Annotated[str, typer.Option("--request-id")],
    create_only: Annotated[bool, typer.Option("--create-only")] = False,
    follow: Annotated[bool, typer.Option("--follow")] = False,
) -> None:
    svc = _service()
    try:
        if create_only:
            run = svc.create_run(goal, request_id=request_id)
        else:
            # task 10.8: run start creates-and-drives; --follow only changes
            # event presentation, not execution semantics (task 10.9).
            run = asyncio.run(svc.start_and_drive(goal, request_id=request_id))
        typer.echo(run.model_dump_json())
        if follow and not create_only:
            # task 10.9: present recent events without changing execution
            with svc.backend.unit_of_work() as uow:
                events = list(uow.events.stream(run.run_id))[-20:]
                uow.commit()
            typer.echo(
                json.dumps(
                    [{"effect": e.effect.value, "state_version": e.state_version} for e in events],
                    ensure_ascii=False,
                ),
                err=True,
            )
    except Exception as exc:  # noqa: BLE001 - stable CLI error mapping
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


@run_app.command("show")
def run_show(run_id: Annotated[str, typer.Argument()]) -> None:
    svc = _service()
    run = svc.get_run(run_id)
    if run is None:
        typer.echo(_diagnostic(KeyError(run_id)), err=True)
        raise typer.Exit(code=404)
    typer.echo(run.model_dump_json())


@run_app.command("watch")
def run_watch(run_id: Annotated[str, typer.Argument()]) -> None:
    svc = _service()
    try:
        report = asyncio.run(svc.drive_run(run_id))
        typer.echo(_advance_json(report))
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


@run_app.command("pause")
def run_pause(
    run_id: Annotated[str, typer.Argument()],
    expected_version: Annotated[int, typer.Option("--expected-version")],
) -> None:
    _mutate(lambda svc: svc.pause_run(run_id, expected_version=expected_version))


@run_app.command("resume")
def run_resume(
    run_id: Annotated[str, typer.Argument()],
    expected_version: Annotated[int, typer.Option("--expected-version")],
) -> None:
    # task 10.12: no arbitrary --target; resume restores the persisted
    # continuation (paused_from_state) and drives to terminal/blocked.
    svc = _service()
    try:
        run = asyncio.run(svc.resume_and_drive(run_id, expected_version=expected_version))
        typer.echo(run.model_dump_json())
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


@run_app.command("cancel")
def run_cancel(
    run_id: Annotated[str, typer.Argument()],
    expected_version: Annotated[int, typer.Option("--expected-version")],
) -> None:
    _mutate(lambda svc: svc.cancel_run(run_id, expected_version=expected_version))


def _mutate(fn) -> None:
    svc = _service()
    try:
        run = fn(svc)
        typer.echo(run.model_dump_json())
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


@gate_app.command("list")
def gate_list(run_id: Annotated[str, typer.Argument()]) -> None:
    svc = _service()
    gates = svc.list_gates(run_id)
    typer.echo(json.dumps([g.model_dump(mode="json") for g in gates], ensure_ascii=False))


@gate_app.command("respond")
def gate_respond(
    gate_id: Annotated[str, typer.Argument()],
    request_id: Annotated[str, typer.Option("--request-id")],
    actor: Annotated[str, typer.Option("--actor")],
    role: Annotated[str, typer.Option("--role")],
    payload: Annotated[str, typer.Option("--payload")] = "{}",
) -> None:
    svc = _service()
    try:
        consumed = svc.respond_gate(
            gate_id,
            request_id=request_id,
            actor=actor,
            role=role,
            payload=json.loads(payload),
        )
        typer.echo(consumed.model_dump_json())
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


@artifact_app.command("list")
def artifact_list() -> None:
    svc = _service()
    typer.echo(
        json.dumps([a.model_dump(mode="json") for a in svc.list_artifacts()], ensure_ascii=False)
    )


@artifact_app.command("export")
def artifact_export(artifact_id: Annotated[str, typer.Argument()]) -> None:
    svc = _service()
    try:
        exported = svc.export_artifact(artifact_id)
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None
    typer.echo(exported.data.decode("utf-8", errors="replace"))


@capability_app.command("list")
def capability_list() -> None:
    svc = _service()
    typer.echo(
        json.dumps([d.model_dump(mode="json") for d in svc.list_capabilities()], ensure_ascii=False)
    )


@capability_app.command("doctor")
def capability_doctor() -> None:
    svc = _service()
    typer.echo(json.dumps(svc.capability_doctor(), ensure_ascii=False))


@runtime_app.command("tick")
def runtime_tick(run_id: Annotated[str, typer.Argument()]) -> None:
    svc = _service()
    try:
        # task 10.10: runtime tick is one bounded RunCoordinator advance.
        report = asyncio.run(svc.advance_run(run_id))
        typer.echo(_advance_json(report))
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


@runtime_app.command("watch")
def runtime_watch(
    run_id: Annotated[str | None, typer.Option("--run")] = None,
) -> None:
    """Watch one Run (``--run``) or all eligible Runs. ``runtime tick`` always
    requires a Run ID (task 11.9); Runtime commands never alter Pause/Gate/Cancel
    state — those are observed by the Tick."""

    svc = _service()
    try:
        if run_id is None:
            reports = asyncio.run(_drive_all(svc))
            typer.echo(json.dumps(reports, ensure_ascii=False))
        else:
            # task 10.11: runtime watch loops RunCoordinator advances.
            report = asyncio.run(svc.drive_run(run_id))
            typer.echo(_advance_json(report))
    except Exception as exc:  # noqa: BLE001
        typer.echo(_diagnostic(exc), err=True)
        raise typer.Exit(code=_exit_for(exc)) from None


def _advance_json(report) -> str:
    """Stable JSON for an AdvanceReport (task 10.14)."""

    return json.dumps(
        {
            "disposition": report.disposition.value,
            "from_state": report.from_state.value,
            "to_state": report.to_state.value,
            "state_version": report.state_version,
            "reason": report.reason,
        },
        ensure_ascii=False,
    )


async def _drive_all(svc: OrchestrationService) -> dict:
    with svc.backend.unit_of_work() as uow:
        run_ids = [run.run_id for run in uow.runs.list_resumable()]
    out: dict[str, dict] = {}
    for rid in run_ids:
        try:
            report = await svc.drive_run(rid)
            out[rid] = {
                "disposition": report.disposition.value,
                "to_state": report.to_state.value,
            }
        except Exception as exc:  # noqa: BLE001 — one Run must not abort the batch
            out[rid] = {"disposition": "error", "to_state": "unknown", "error": str(exc)}
    return out


if __name__ == "__main__":  # pragma: no cover
    app()

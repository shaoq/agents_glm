"""Typer CLI entry point.

First-release commands (implemented in Section 11)::

    run start/show/watch/pause/resume/cancel
    gate list/respond
    artifact list/export
    capability list/doctor
    runtime tick RUN_ID | watch [--run RUN_ID]

The CLI is a thin adapter over :mod:`agents_orchestration.application`; it only
performs argument adaptation and presentation. This scaffold exposes a runnable
``app`` with a version flag so the entry point can be imported and exercised.
"""

from __future__ import annotations

import typer

from agents_orchestration import __version__

app = typer.Typer(
    name="agents-orchestration",
    help="Local-first durable orchestration runtime for intelligent research.",
)


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the package version and exit.",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit
    if ctx.invoked_subcommand is None:
        typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()

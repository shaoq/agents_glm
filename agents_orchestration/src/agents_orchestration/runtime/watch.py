"""Single-process Runtime Watch: drive one Run or all eligible Runs by looping
Ticks until terminal or blocked (task 4.10).

Only one continuous Watch process is supported in the first release (design
Risks). Watch does not alter Pause/Gate/Cancel state — those are observed by the
Tick (task 11.9).
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_orchestration.domain.enums import TerminationReason


@dataclass(frozen=True)
class WatchReport:
    run_id: str
    ticks: int
    terminal: bool
    blocked: bool
    termination: TerminationReason | None = None


class RuntimeWatch:
    def __init__(self, backend, tick) -> None:
        self.backend = backend
        self.tick = tick

    async def drive_run(self, run_id: str, *, max_ticks: int = 1000) -> WatchReport:
        ticks = 0
        terminal = blocked = False
        termination: TerminationReason | None = None
        for _ in range(max_ticks):
            report = await self.tick.tick(run_id)
            ticks += 1
            terminal = report.terminal
            blocked = report.blocked
            termination = report.termination
            if terminal or blocked:
                break
            if report.dispatched == 0:
                blocked = True
                break
        return WatchReport(run_id, ticks, terminal, blocked, termination)

    async def drive_all(self, *, max_ticks_per_run: int = 1000) -> dict[str, WatchReport]:
        with self.backend.unit_of_work() as uow:
            run_ids = [run.run_id for run in uow.runs.list_resumable()]
        reports: dict[str, WatchReport] = {}
        for run_id in run_ids:
            reports[run_id] = await self.drive_run(run_id, max_ticks=max_ticks_per_run)
        return reports

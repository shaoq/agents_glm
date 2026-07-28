"""Watch edge cases + recovery hardening tests (tasks 10.7, 11.4, 11.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    InputFingerprint,
    PhaseId,
    StageExecution,
    StageStatus,
    build_gate_continuation,
    stage_logical_key,
)
from agents_orchestration.domain.enums import GateType, RunState
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
    return OrchestrationService(
        SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=_Clock())
    )


# --- Task 10.7: Watch edge cases ------------------------------------------


@pytest.mark.integration
async def test_drive_run_open_gate_blocks(service: OrchestrationService) -> None:
    run = service.create_run("goal", request_id="r1")
    # open a gate on the NORMALIZING run so the coordinator reports BLOCKED
    with service.backend.unit_of_work() as uow:
        GateService(uow, service.backend.clock, service.backend.idgen).open(
            run,
            GateType.GOAL_CLARIFICATION,
            actor="system",
            role="orchestrator",
            scope=run.run_id,
            allowed_response_schema="{}",
            continuation=build_gate_continuation(GateType.GOAL_CLARIFICATION, run),
        )
        uow.commit()
    report = await service.drive_run(run.run_id)
    assert report.disposition is AdvanceDisposition.BLOCKED


@pytest.mark.integration
async def test_drive_run_terminal_run_reports_terminal(service: OrchestrationService) -> None:
    run = Run(
        run_id=service.backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.SUCCEEDED,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )
    with service.backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    report = await service.drive_run(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL


@pytest.mark.integration
async def test_drive_run_max_advances_bounds_loops(service: OrchestrationService) -> None:
    run = service.create_run("goal", request_id="r2")
    await service.drive_run(run.run_id, max_advances=1)  # one advance only
    # CREATED -> NORMALIZING in the single allowed advance
    assert service.get_run(run.run_id).state is RunState.NORMALIZING


@pytest.mark.integration
async def test_drive_run_rejects_non_positive_max_advances(
    service: OrchestrationService,
) -> None:
    with pytest.raises(ValueError):
        await service.drive_run("any", max_advances=0)


# --- Task 11.4 / 11.5: recovery — PREPARED reuse + legacy Runs ------------


@pytest.mark.integration
async def test_restart_reuses_accepted_stage_record(service: OrchestrationService) -> None:
    """An accepted StageExecution for a logical stage+fingerprint is reused
    across a restart (11.4): the repository's prepare is idempotent, so the
    accepted result is not duplicated (11.7) and the provider need not rerun."""

    run = service.create_run("goal", request_id="r3")
    await service.advance_run(run.run_id)  # CREATED -> NORMALIZING (accepts a stage)
    fp = InputFingerprint(state_version=run.state_version)
    key = stage_logical_key(PhaseId.GOAL)
    # Simulate a first accepted stage record, then a restart replay.
    with service.backend.unit_of_work() as uow:
        stage = StageExecution(
            stage_execution_id="se-1",
            run_id=run.run_id,
            phase=PhaseId.GOAL,
            logical_stage_key=key,
            fingerprint=fp,
            status=StageStatus.PREPARED,
            idempotency_key=f"{run.run_id}|{key}|{fp.hexdigest()}",
            created_at=NOW,
            updated_at=NOW,
        )
        uow.stages.prepare(stage)
        uow.stages.accept("se-1", accepted=stage.transition(StageStatus.ACCEPTED, at=NOW))
        uow.commit()
    # Replay after "restart": same logical key + fingerprint -> reuse, no new row.
    with service.backend.unit_of_work() as uow:
        replay = StageExecution(
            stage_execution_id="se-2",
            run_id=run.run_id,
            phase=PhaseId.GOAL,
            logical_stage_key=key,
            fingerprint=fp,
            status=StageStatus.PREPARED,
            idempotency_key=f"{run.run_id}|{key}|{fp.hexdigest()}",
            created_at=NOW,
            updated_at=NOW,
        )
        reused = uow.stages.prepare(replay)
        assert reused.stage_execution_id == "se-1"  # 11.4: accepted result reused
        uow.commit()


@pytest.mark.integration
async def test_legacy_run_without_stage_records_advances(
    service: OrchestrationService,
) -> None:
    """A legacy non-terminal Run (no StageExecution records) still advances via
    state-driven recovery (11.5) — the coordinator resumes from phase_for_state
    without requiring reconstructed stage records."""

    run = Run(
        run_id=service.backend.idgen.new_id("run"),
        raw_goal="clear goal",
        state=RunState.NORMALIZING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )
    with service.backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    # No stage records exist (legacy). drive still reaches terminal.
    await service.drive_run(run.run_id)
    assert service.get_run(run.run_id).state is RunState.SUCCEEDED

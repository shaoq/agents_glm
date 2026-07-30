"""Legacy Plan reconciliation tests (remove-noop-phase-tasks 5.x).

A pre-upgrade Plan may carry analyst/report_writer/report_reviewer Tasks that
the new research-only contract rejects. The coordinator retires them lazily on
the next advance (PENDING/READY → SKIPPED; DISPATCHED/AWAITING_RETRY → CANCELED
with active leases invalidated) without dispatching them, and the operation is
idempotent. Tasks are seeded directly (bypassing PlanValidator, which now
rejects non-research roles) to emulate a legacy in-flight Plan.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.enums import RunState, TaskState, WorkerRole
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.lifecycle import Lease, LeaseState
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from tests.support.deterministic import build_deterministic_coordinator

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _seed_run(backend, state: RunState = RunState.ANALYZING) -> Run:
    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=state,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    return run


def _seed_task(backend, run, task_id, role, state) -> None:
    task = Task(
        task_id=task_id,
        run_id=run.run_id,
        plan_version=run.current_plan_version or 1,
        worker_role=role,
        state=state,
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.tasks.materialize([task])
        uow.commit()


@pytest.mark.integration
async def test_legacy_pending_non_research_tasks_are_skipped(backend) -> None:
    """remove-noop-phase-tasks 5.2: PENDING/READY legacy Tasks → SKIPPED."""

    run = _seed_run(backend, RunState.ANALYZING)
    _seed_task(backend, run, "legacy-analyst", WorkerRole.ANALYST, TaskState.PENDING)
    _seed_task(backend, run, "legacy-writer", WorkerRole.REPORT_WRITER, TaskState.READY)

    coord = build_deterministic_coordinator(backend)
    await coord.advance(run.run_id)

    with backend.unit_of_work() as uow:
        assert uow.tasks.get("legacy-analyst").state is TaskState.SKIPPED
        assert uow.tasks.get("legacy-writer").state is TaskState.SKIPPED
        uow.commit()


@pytest.mark.integration
async def test_legacy_dispatched_non_research_tasks_canceled_with_lease_invalidated(
    backend,
) -> None:
    """remove-noop-phase-tasks 5.2/5.3: DISPATCHED legacy Tasks → CANCELED and
    their active lease is invalidated so late results cannot advance the Run."""

    run = _seed_run(backend, RunState.ANALYZING)
    _seed_task(backend, run, "legacy-analyst", WorkerRole.ANALYST, TaskState.DISPATCHED)
    with backend.unit_of_work() as uow:
        uow.leases.save(
            Lease(
                task_id="legacy-analyst",
                attempt_id="att-x",
                run_id=run.run_id,
                epoch=1,
                state=LeaseState.CLAIMED,
                claimed_at=NOW,
                expires_at=NOW,
            )
        )
        uow.commit()

    coord = build_deterministic_coordinator(backend)
    await coord.advance(run.run_id)

    with backend.unit_of_work() as uow:
        assert uow.tasks.get("legacy-analyst").state is TaskState.CANCELED
        assert uow.leases.get("legacy-analyst").state is LeaseState.EXPIRED
        uow.commit()


@pytest.mark.integration
async def test_legacy_reconciliation_is_idempotent(backend) -> None:
    """remove-noop-phase-tasks 5.6: a second advance does not reprocess a Task
    that the first advance already terminalized."""

    run = _seed_run(backend, RunState.ANALYZING)
    _seed_task(backend, run, "legacy-analyst", WorkerRole.ANALYST, TaskState.PENDING)

    coord = build_deterministic_coordinator(backend)
    await coord.advance(run.run_id)  # ANALYZING -> WRITING, legacy -> SKIPPED
    await coord.advance(run.run_id)  # WRITING -> REVIEWING, legacy already terminal

    with backend.unit_of_work() as uow:
        task = uow.tasks.get("legacy-analyst")
        assert task.state is TaskState.SKIPPED  # still SKIPPED, not reprocessed
        uow.commit()

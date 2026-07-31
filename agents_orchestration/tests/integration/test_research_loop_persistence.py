"""SQLite invariants for durable research loop records."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.plan import (
    ExplorationBoundary,
    Plan,
    PlanGraph,
    ResearchExecutionMode,
    SeedExplorationBoundary,
)
from agents_orchestration.domain.research_loop import (
    ResearchDirection,
    ResearchLoop,
    ResearchLoopStatus,
    ResearchStep,
    ResearchStepStatus,
)
from agents_orchestration.runtime.ports import ConcurrencyError


def _now() -> datetime:
    return datetime(2026, 7, 31, tzinfo=UTC)


def _loop(*, version: int = 1) -> ResearchLoop:
    return ResearchLoop(
        loop_id="loop-1",
        run_id="r1",
        plan_version=1,
        task_id="seed-1",
        status=ResearchLoopStatus.ACTIVE,
        state_version=version,
        created_at=_now(),
        updated_at=_now(),
    )


def _step(
    *,
    step_id: str = "step-0",
    attempt_id: str = "att-1",
    status: ResearchStepStatus = ResearchStepStatus.DECIDING,
) -> ResearchStep:
    return ResearchStep(
        step_id=step_id,
        loop_id="loop-1",
        run_id="r1",
        plan_version=1,
        task_id="seed-1",
        step_index=0,
        status=status,
        decision_request_id="decision:step-0",
        capability_request_id="capability:step-0",
        attempt_id=attempt_id,
        lease_epoch=1,
        state_version_at_dispatch=1,
        created_at=_now(),
        updated_at=_now(),
    )


@pytest.mark.integration
def test_loop_direction_and_step_round_trip(backend) -> None:
    direction = ResearchDirection(
        direction_id="dir-1",
        loop_id="loop-1",
        run_id="r1",
        plan_version=1,
        task_id="seed-1",
        text="[UNTRUSTED_DIRECTION] seed",
        focus_hash="focus-1",
        capability_scope=(CapabilityKind.RAG_SEARCH,),
        created_at=_now(),
    )
    with backend.unit_of_work() as uow:
        uow.research_loops.save(_loop(), expected_version=None)
        uow.research_directions.save(direction)
        uow.research_steps.save(_step(), expected_status=None)
        uow.commit()

    with backend.unit_of_work() as uow:
        assert uow.research_loops.for_task("r1", 1, "seed-1") == _loop()
        assert uow.research_directions.by_focus_hash("loop-1", "focus-1") == direction
        assert uow.research_steps.active_for_task("r1", 1, "seed-1") == _step()


@pytest.mark.integration
def test_loop_cas_and_logical_step_unique_constraints(backend) -> None:
    with backend.unit_of_work() as uow:
        uow.research_loops.save(_loop(), expected_version=None)
        uow.research_steps.save(_step(), expected_status=None)
        uow.commit()

    with backend.unit_of_work() as uow:
        with pytest.raises(ConcurrencyError):
            uow.research_loops.save(_loop(version=2), expected_version=99)
        uow.rollback()

    duplicate_logical_step = _step(step_id="step-duplicate", attempt_id="att-2")
    with backend.unit_of_work() as uow:
        with pytest.raises(ConcurrencyError, match="logical research step"):
            uow.research_steps.save(duplicate_logical_step, expected_status=None)
        uow.rollback()


@pytest.mark.integration
def test_direction_focus_hash_is_deduplicated_by_storage(backend) -> None:
    first = ResearchDirection(
        direction_id="dir-1",
        loop_id="loop-1",
        run_id="r1",
        plan_version=1,
        task_id="seed-1",
        text="[UNTRUSTED_DIRECTION] seed",
        focus_hash="same",
        capability_scope=(CapabilityKind.RAG_SEARCH,),
        created_at=_now(),
    )
    second = first.model_copy(update={"direction_id": "dir-2"})
    with backend.unit_of_work() as uow:
        uow.research_directions.save(first)
        with pytest.raises(ConcurrencyError, match="focus hash"):
            uow.research_directions.save(second)
        uow.rollback()

    with backend.unit_of_work() as uow:
        assert uow.research_directions.by_loop("loop-1") == []


@pytest.mark.integration
def test_research_accept_bundle_rolls_back_together(backend) -> None:
    with backend.unit_of_work() as uow:
        uow.research_loops.save(_loop(), expected_version=None)
        uow.research_steps.save(_step(), expected_status=None)
        uow.rollback()

    with backend.unit_of_work() as uow:
        assert uow.research_loops.get("loop-1") is None
        assert uow.research_steps.get("step-0") is None


@pytest.mark.integration
def test_plan_mode_is_immutable_for_same_run_version(backend) -> None:
    fixed = Plan(
        run_id="r1",
        graph=PlanGraph(plan_id="p1", version=1),
        proposed_at=_now(),
    )
    loop = fixed.model_copy(
        update={
            "graph": PlanGraph(
                plan_id="p1",
                version=1,
                research_execution_mode=ResearchExecutionMode.AGENT_LOOP,
                exploration_boundary=ExplorationBoundary(
                    allowed_capabilities=(CapabilityKind.RAG_SEARCH,),
                    seeds=(
                        SeedExplorationBoundary(
                            task_id="seed-1",
                            required_coverage=(CapabilityKind.RAG_SEARCH,),
                            max_steps=2,
                            max_directions=1,
                        ),
                    ),
                ),
            )
        }
    )
    with backend.unit_of_work() as uow:
        uow.plans.save(fixed)
        uow.commit()
    with backend.unit_of_work() as uow:
        with pytest.raises(ConcurrencyError, match="execution mode"):
            uow.plans.save(loop)
        uow.rollback()

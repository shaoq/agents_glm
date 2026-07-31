"""Domain contracts for the durable research-agent loop."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.plan import (
    ExplorationBoundary,
    PlanGraph,
    ResearchExecutionMode,
    SeedExplorationBoundary,
)
from agents_orchestration.domain.research_loop import (
    AddDirectionAction,
    QueryAction,
    ResearchAction,
    ResearchActionEnvelope,
    ResearchDirection,
    ResearchLoop,
    ResearchLoopStatus,
    ResearchStep,
    ResearchStepStatus,
    StopRequestAction,
)


def _seed_boundary(task_id: str = "seed-1") -> SeedExplorationBoundary:
    return SeedExplorationBoundary(
        task_id=task_id,
        required_coverage=(CapabilityKind.RAG_SEARCH,),
        max_steps=5,
        max_directions=2,
        max_tokens=1_000,
        max_cost_usd=Decimal("1.25"),
    )


def test_legacy_plan_json_defaults_to_fixed_fanout() -> None:
    graph = PlanGraph.model_validate(
        {
            "plan_id": "p1",
            "version": 1,
            "task_specs": [],
            "dependencies": [],
            "deliverable_paths": [],
        }
    )

    assert graph.schema_version == 1
    assert graph.research_execution_mode is ResearchExecutionMode.FIXED_FANOUT
    assert graph.exploration_boundary is None


def test_agent_loop_plan_requires_boundary() -> None:
    with pytest.raises(ValidationError, match="exploration_boundary"):
        PlanGraph(
            plan_id="p1",
            research_execution_mode=ResearchExecutionMode.AGENT_LOOP,
        )


def test_boundary_rejects_required_coverage_outside_allowlist() -> None:
    with pytest.raises(ValidationError, match="required coverage"):
        ExplorationBoundary(
            allowed_capabilities=(CapabilityKind.MEMORY_RECALL,),
            seeds=(_seed_boundary(),),
        )


def test_action_union_rejects_formal_state_injection() -> None:
    payload = {
        "kind": "query",
        "direction_id": "dir-1",
        "capability_kind": "rag_search",
        "query": "trusted query",
        "rationale": "need evidence",
        "run_state": "succeeded",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TypeAdapter(ResearchAction).validate_python(payload)


def test_action_envelope_binds_formal_identity_without_exposing_request_id() -> None:
    envelope = ResearchActionEnvelope(
        run_id="r1",
        plan_version=2,
        task_id="seed-1",
        loop_id="loop-1",
        step_id="step-3",
        action=QueryAction(
            direction_id="dir-1",
            capability_kind=CapabilityKind.RAG_SEARCH,
            query="what changed?",
            rationale="cover the seed",
        ),
    )

    dumped = envelope.model_dump()
    assert dumped["step_id"] == "step-3"
    assert "request_id" not in dumped["action"]
    assert "capability_id" not in dumped["action"]


def test_loop_direction_and_step_are_frozen_durable_records() -> None:
    now = datetime(2026, 7, 31, tzinfo=UTC)
    loop = ResearchLoop(
        loop_id="loop-1",
        run_id="r1",
        plan_version=1,
        task_id="seed-1",
        status=ResearchLoopStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    direction = ResearchDirection(
        direction_id="dir-1",
        loop_id=loop.loop_id,
        run_id=loop.run_id,
        plan_version=loop.plan_version,
        task_id=loop.task_id,
        text="[UNTRUSTED_DIRECTION] investigate claim",
        focus_hash="abc",
        capability_scope=(CapabilityKind.RAG_SEARCH,),
        source_step_id=None,
        created_at=now,
    )
    step = ResearchStep(
        step_id="step-0",
        loop_id=loop.loop_id,
        run_id=loop.run_id,
        plan_version=loop.plan_version,
        task_id=loop.task_id,
        step_index=0,
        status=ResearchStepStatus.DECIDING,
        decision_request_id="decision:step-0",
        capability_request_id="capability:step-0",
        attempt_id="att-1",
        lease_epoch=1,
        state_version_at_dispatch=1,
        created_at=now,
        updated_at=now,
    )

    assert direction.parent_direction_id is None
    assert step.retry_count == 0
    with pytest.raises(ValidationError):
        ResearchActionEnvelope(
            run_id="r1",
            plan_version=1,
            task_id="seed-1",
            loop_id="loop-1",
            step_id="step-0",
            action=AddDirectionAction(
                parent_direction_id="dir-1",
                hint="",
                rationale="x",
            ),
        )


@pytest.mark.parametrize(
    "action",
    [
        QueryAction(
            direction_id="dir-1",
            capability_kind=CapabilityKind.RAG_SEARCH,
            query="q",
            rationale="r",
        ),
        AddDirectionAction(parent_direction_id="dir-1", hint="h", rationale="r"),
        StopRequestAction(
            reason="done",
            supporting_evidence_ids=("ev-1",),
            unresolved_questions=(),
        ),
    ],
)
def test_all_action_variants_round_trip(action: ResearchAction) -> None:
    parsed = TypeAdapter(ResearchAction).validate_python(action.model_dump(mode="json"))
    assert parsed == action

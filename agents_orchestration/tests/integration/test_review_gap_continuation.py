"""REVIEW research-gap Gate continuation -> shared Focused Replan (tasks 7.4 / 7.5)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.coordination import PhaseId
from agents_orchestration.domain.enums import (
    GateType,
    ReviewVerdict,
    RunState,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.orchestration.phases import ReviewPhaseHandler
from agents_orchestration.orchestration.report import ReportContent, ReviewProposal
from tests.support.service_factory import build_test_service


async def _report_provider(run_id: str) -> ReportContent:
    return ReportContent(run_id=run_id, title="T", objective="O")


class _GapReviewer:
    async def __call__(self, run_id: str, report) -> ReviewProposal:
        return ReviewProposal(verdict=ReviewVerdict.RESEARCH_GAP, reason="evidence gap")


def _service_with_review_gap(backend):
    service = build_test_service(backend)
    service.coordinator.handlers[PhaseId.REVIEW] = ReviewPhaseHandler(
        _GapReviewer(),
        _report_provider,
        clock=backend.clock,
        idgen=backend.idgen,
    )
    return service


def _conflict_gate(service, run_id):
    for gate in service.list_gates(run_id):
        if gate.gate_type is GateType.CONFLICT_RESOLUTION:
            return gate
    raise AssertionError("no CONFLICT_RESOLUTION gate opened")


# --- 7.1: opening the Gate does not consume replan budget -----------------


@pytest.mark.integration
async def test_review_gap_opens_gate_without_consuming_replan_budget(backend) -> None:
    service = _service_with_review_gap(backend)
    run = await service.start_and_drive("clear goal", request_id="rg-1")
    assert run.state is RunState.REVIEWING
    gate = _conflict_gate(service, run.run_id)
    assert gate.continuation.intent == "review_research_gap"
    assert gate.continuation.feedback == "evidence gap"
    assert gate.continuation.correlation_id.startswith("gap:")
    assert service.get_run(run.run_id).replan_count == 0  # 7.1: zero count at open


# --- 7.2: 'resolved' continuation runs the shared Focused Replan ----------


@pytest.mark.integration
async def test_resolved_continuation_creates_focused_replan(backend) -> None:
    service = _service_with_review_gap(backend)
    run = await service.start_and_drive("clear goal", request_id="rg-2")
    gate = _conflict_gate(service, run.run_id)

    consumed = service.respond_gate(
        gate.gate_id,
        request_id="resp-1",
        actor="reviewer",
        role="orchestrator",
        payload={"outcome": "resolved", "resolution": "collect competitor pricing"},
    )
    assert consumed.is_consumed

    moved = service.get_run(run.run_id)
    assert moved.state is RunState.RESEARCHING
    assert moved.replan_count == 1  # one Plan version change
    assert moved.current_plan_version == 2
    with backend.unit_of_work() as uow:
        new_pending = [
            t
            for t in uow.tasks.by_run(run.run_id, plan_version=2)
            if t.worker_role is WorkerRole.EVIDENCE_RESEARCHER and t.state is TaskState.PENDING
        ]
        effects = [e.effect.value for e in uow.events.stream(run.run_id)]
        uow.commit()
    assert len(new_pending) >= 1  # new PENDING research task
    assert "evidence gap" in new_pending[0].description
    assert "collect competitor pricing" not in new_pending[0].description
    assert "plan_replanned" in effects


# --- 7.3: budget exhausted at continuation -> deterministic terminate ------


@pytest.mark.integration
async def test_resolved_continuation_with_exhausted_budget_terminates(backend) -> None:
    service = _service_with_review_gap(backend)
    run = await service.start_and_drive("clear goal", request_id="rg-3")
    gate = _conflict_gate(service, run.run_id)
    # Burn the shared replan budget before the responder confirms.
    with backend.unit_of_work() as uow:
        current = uow.runs.get(run.run_id)
        uow.runs.save(
            current.model_copy(update={"replan_count": current.policy.max_replans}),
            expected_version=current.state_version,
        )
        uow.commit()

    service.respond_gate(
        gate.gate_id,
        request_id="resp-2",
        actor="reviewer",
        role="orchestrator",
        payload={"outcome": "resolved", "resolution": "more research"},
    )
    moved = service.get_run(run.run_id)
    assert moved.state is RunState.FAILED
    assert moved.termination is TerminationReason.REQUIRED_EVIDENCE_MISSING
    with backend.unit_of_work() as uow:
        plan = uow.plans.current(run.run_id)
        uow.commit()
    assert plan.version == 1  # no Plan/Task created on termination


# --- 7.4: duplicate response is idempotent (at-most-once) ----------------


@pytest.mark.integration
async def test_duplicate_gate_response_is_rejected(backend) -> None:
    from agents_orchestration.orchestration.gates import GateResponseError

    service = _service_with_review_gap(backend)
    run = await service.start_and_drive("clear goal", request_id="rg-4")
    gate = _conflict_gate(service, run.run_id)
    service.respond_gate(
        gate.gate_id,
        request_id="resp-dup",
        actor="reviewer",
        role="orchestrator",
        payload={"outcome": "resolved", "resolution": "x"},
    )
    # A second response is rejected (gate already consumed — at-most-once, 7.4).
    with pytest.raises(GateResponseError):
        service.respond_gate(
            gate.gate_id,
            request_id="resp-dup",
            actor="reviewer",
            role="orchestrator",
            payload={"outcome": "resolved", "resolution": "x"},
        )


# --- 7.5: existing REVIEW PASS/CONFLICT/Gate vocabulary unchanged --------


@pytest.mark.integration
async def test_review_pass_still_advances_to_finalizing(backend) -> None:
    """Regression: the non-gap REVIEW verdicts and Gate semantics are unchanged."""

    service = build_test_service(backend)  # default FakeReviewer returns PASS
    run = await service.start_and_drive("clear goal", request_id="rg-pass")
    assert run.state is RunState.SUCCEEDED  # PASS -> FINALIZE -> SUCCEEDED

"""E2E for the four Human Gates (13.2) and degradation disclosure (13.4)."""

from __future__ import annotations

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.enums import (
    CapabilityKind,
    CompletionState,
    GateState,
    GateType,
    RunState,
    Sufficiency,
    TaskState,
    TerminationReason,
)
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.orchestration.gates import GateService
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    Finalizer,
    ReportBuilder,
    ReportContent,
)
from tests.e2e.test_research_run import _accept_plan, _research_spec


def _seed_research_run(service: OrchestrationService, request_id: str, task_id="t1"):
    run = service.start_run("goal", request_id=request_id)
    run, contract = _accept_plan(service, run.run_id, (_research_spec(task_id),))
    return run, contract


# --- 13.2 all four gate types block the run and resume after consume --------


@pytest.mark.e2e
@pytest.mark.parametrize("gate_type", list(GateType))
async def test_each_gate_type_blocks_and_resumes(service, gate_type) -> None:
    run, _ = _seed_research_run(service, "e2e-gate-" + gate_type.value)
    with service.backend.unit_of_work() as uow:
        gate = GateService(uow, service.backend.clock, service.backend.idgen).open(
            uow.runs.get(run.run_id),
            gate_type,
            actor="user",
            role="approver",
            scope="run",
            allowed_response_schema="{}",
            ttl_seconds=3600,
        )
        uow.commit()
    assert gate.gate_type is gate_type

    blocked = await service.tick(run.run_id).tick(run.run_id)
    assert blocked.blocked

    with service.backend.unit_of_work() as uow:
        svc = GateService(uow, service.backend.clock, service.backend.idgen)
        gate = uow.gates.get(gate.gate_id)
        responded = svc.respond(
            gate,
            request_id="rq-" + gate_type.value,
            actor="user",
            role="approver",
            payload={},
        )
        consumed = svc.consume(responded)
        uow.commit()
    assert consumed.state is GateState.CONSUMED

    resumed = await service.tick(run.run_id).tick(run.run_id)
    assert resumed.dispatched == 1


# --- 13.4 degradation is disclosed, not faked as success --------------------


@pytest.mark.e2e
async def test_empty_memory_lane_degrades_and_finalize_reports_partial(
    empty_memory_service,
) -> None:
    service = empty_memory_service
    run = service.start_run("goal needing memory", request_id="e2e-degrade")
    # Memory-backed research task.
    run, contract = _accept_plan(
        service,
        run.run_id,
        (_research_spec("mem-task", caps=(CapabilityKind.MEMORY_RECALL,)),),
    )
    await service.drive_run(run.run_id)
    with service.backend.unit_of_work() as uow:
        # The task succeeds (a result was produced) but with no evidence.
        assert uow.tasks.get("mem-task").state is TaskState.SUCCEEDED

    # Finalize with the (empty) evidence set -> completion UNSATISFIED, partial.
    empty_evidence = EvidenceSet(
        run_id=run.run_id,
        evidences=(),
        sufficiency=Sufficiency.INSUFFICIENT,
        missing_required=True,
        independent_count=0,
    )
    report = ReportContent(run_id=run.run_id, title="Report", objective="goal")
    with service.backend.unit_of_work() as uow:
        artifacts = ReportBuilder().build(
            uow.artifacts,
            run_id=run.run_id,
            report=report,
            analysis=AnalysisArtifact(run_id=run.run_id),
            completion_overall=CompletionState.UNSATISFIED,
            evidence=empty_evidence,
            degradations=(),
            termination=None,
            clock=service.backend.clock,
        )
        run_obj = uow.runs.get(run.run_id)
        _terminal, reason = Finalizer().finalize(
            uow,
            run_obj,
            artifacts=artifacts,
            completion_overall=CompletionState.UNSATISFIED,
            clock=service.backend.clock,
            idgen=service.backend.idgen,
        )
        uow.commit()
    with service.backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.FAILED
        assert final.termination is TerminationReason.REQUIRED_EVIDENCE_MISSING
        summary = uow.artifacts.read(artifacts.run_summary)
        assert b'"unmet": true' in summary

"""End-to-end research run tests with deterministic test doubles (13.1/13.3/13.6/13.8)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import (
    CapabilityKind,
    CompletionState,
    RunState,
    Sufficiency,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.evidence import Evidence, EvidenceSet, SourceIdentity, SourceKind
from agents_orchestration.domain.goal import CompletionContract, CompletionCriterion, CriterionKind
from agents_orchestration.domain.lifecycle import Lease
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import PlanProposal, ReplanProposal
from agents_orchestration.orchestration.replan import ReplanService
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    Finalizer,
    ReportBuilder,
    ReportContent,
    ReportSection,
)
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from tests.support.service_factory import build_test_service


def _contract(deliverable: str = "report.md") -> CompletionContract:
    # Deterministic e2e has no real evidence, so the contract only requires the
    # final deliverable (report.md); FINALIZE evaluates it against
    # deliverables_provider and reaches SUCCEEDED (remove-noop-phase-tasks: the
    # Writing phase owns report.md, no research Task claims it).
    return CompletionContract(
        criteria=(
            CompletionCriterion(
                kind=CriterionKind.DELIVERABLE,
                description=deliverable,
                deliverable_path=deliverable,
            ),
        ),
        deliverable_paths=(deliverable,),
    )


def _accept_plan(service, run_id, task_specs, deliverable="report.md"):
    contract = _contract(deliverable)
    with service.backend.unit_of_work() as uow:
        run = uow.runs.get(run_id)
        # Goal normalization is assumed done; move NORMALIZING -> PLANNING so the
        # PlanAcceptor can transition PLANNING -> RESEARCHING on acceptance.
        if run.state is RunState.NORMALIZING:
            planned = run.transition(RunState.PLANNING, service.backend.clock.now())
            uow.runs.save(planned, expected_version=run.state_version)
            run = planned
        proposal = PlanProposal(
            run_id=run_id,
            plan_id="p1",
            task_specs=task_specs,
            deliverable_paths=(deliverable,),
        )
        validation = PlanValidator(service.limits).validate(
            proposal,
            policy=run.policy,
            allowed_capabilities=service.capability_registry.allowed_kinds(),
            completion=contract,
        )
        assert validation.accepted, validation.diagnostics
        _plan, run = PlanAcceptor(uow, service.backend.clock, service.backend.idgen).accept(
            run, proposal, validation
        )
        uow.completion.save(run_id, contract)
        uow.commit()
    return run, contract


def _research_spec(task_id: str, caps=(CapabilityKind.RAG_SEARCH,)) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        required_capabilities=caps,
        description=task_id,
    )


def _finalize(service, run_id, contract, *, completion=CompletionState.SATISFIED) -> None:
    evidence = EvidenceSet(
        run_id=run_id,
        evidences=(
            Evidence(
                evidence_id="rag-1",
                source=SourceIdentity(source_id="kb:s1", source_kind=SourceKind.RAG, uri="kb:s1"),
                content_text="passage",
            ),
        ),
        sufficiency=Sufficiency.SUFFICIENT,
        independent_count=1,
    )
    report = ReportContent(
        run_id=run_id,
        title="Research Report",
        objective=run_id,
        sections=(ReportSection(title="Findings", body="body", cited_evidence_ids=("rag-1",)),),
        conclusions=("X holds",),
        cited_evidence_ids=("rag-1",),
    )
    with service.backend.unit_of_work() as uow:
        artifacts = ReportBuilder().build(
            uow.artifacts,
            run_id=run_id,
            report=report,
            analysis=AnalysisArtifact(
                run_id=run_id, conclusions=("X holds",), cited_evidence_ids=("rag-1",)
            ),
            completion_overall=completion,
            evidence=evidence,
            degradations=(),
            termination=None,
            clock=service.backend.clock,
        )
        run = uow.runs.get(run_id)
        Finalizer().finalize(
            uow,
            run,
            artifacts=artifacts,
            completion_overall=completion,
            clock=service.backend.clock,
            idgen=service.backend.idgen,
        )
        uow.commit()


# --- 13.1 full deterministic research run -----------------------------------


@pytest.mark.e2e
async def test_full_research_run_goal_plan_research_finalize(service) -> None:
    run = service.start_run("summarize X", request_id="e2e-full")
    run, _contract_used = _accept_plan(service, run.run_id, (_research_spec("research-1"),))
    assert run.state is RunState.RESEARCHING

    await service.drive_run(run.run_id)

    with service.backend.unit_of_work() as uow:
        assert uow.tasks.get("research-1").state is TaskState.SUCCEEDED
        # The research stage emitted dispatched/accepted events.
        effects = [e.effect.value for e in uow.events.stream(run.run_id)]
        assert "task_dispatched" in effects and "attempt_accepted" in effects

    # drive_run now advances through ANALYZE/WRITE/REVIEW/FINALIZE directly via
    # coordinator-owned ports (remove-noop-phase-tasks), so the Run reaches
    # SUCCEEDED and persists the 3 report artifacts without a manual finalize.
    with service.backend.unit_of_work() as uow:
        final = uow.runs.get(run.run_id)
        assert final.state is RunState.SUCCEEDED
        assert final.termination is TerminationReason.COMPLETED
        # Report artifacts persisted and content-addressed.
        assert len(uow.artifacts.list_all()) == 3


# --- 13.3 evidence-gap Replan preserves accepted results --------------------


@pytest.mark.e2e
@pytest.mark.xfail(
    reason="manual E2E; task 12.1 replaces it with a public start_and_drive E2E "
    "(coordinator-backed drive_run changes phase semantics)",
    strict=False,
)
async def test_replan_preserves_accepted_and_adds_focused_task(service) -> None:
    run = service.start_run("research Y", request_id="e2e-replan")
    run, _ = _accept_plan(service, run.run_id, (_research_spec("gap-a"),))
    await service.drive_run(run.run_id)
    with service.backend.unit_of_work() as uow:
        assert uow.tasks.get("gap-a").state is TaskState.SUCCEEDED

    proposal = ReplanProposal(
        run_id=run.run_id,
        reason="evidence_gap",
        add_task_specs=(_research_spec("gap-b"),),
    )
    with service.backend.unit_of_work() as uow:
        run = uow.runs.get(run.run_id)
        ReplanService(
            uow,
            PlanValidator(service.limits),
            PlanAcceptor(uow, service.backend.clock, service.backend.idgen),
            service.backend.clock,
            service.backend.idgen,
        ).replan(run, proposal)
        uow.commit()

    await service.drive_run(run.run_id)
    with service.backend.unit_of_work() as uow:
        assert uow.tasks.get("gap-a").state is TaskState.SUCCEEDED  # preserved
        assert uow.tasks.get("gap-b").state is TaskState.SUCCEEDED  # added + ran
        assert uow.runs.get(run.run_id).replan_count == 1


# --- 13.6 restart resumes from SQLite + Artifact in a fresh process --------


@pytest.mark.e2e
async def test_restart_resumes_in_fresh_process(tmp_path, fake_clock) -> None:
    backend_a = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=fake_clock)
    service_a = build_test_service(backend_a)
    run = service_a.start_run("research Z", request_id="e2e-restart")
    run, _ = _accept_plan(service_a, run.run_id, (_research_spec("rs-1"),))
    # Simulate a mid-dispatch crash: task DISPATCHED with an already-expired lease.
    now = fake_clock.now()
    with backend_a.unit_of_work() as uow:
        task = uow.tasks.get("rs-1").transition(TaskState.DISPATCHED, now, attempt_count=1)
        uow.tasks.save(task)
        from agents_orchestration.domain.execution import Attempt

        uow.attempts.save(
            Attempt(
                attempt_id="a1",
                task_id="rs-1",
                run_id=run.run_id,
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                lease_epoch=1,
                plan_version=1,
                state_version_at_dispatch=run.state_version,
                started_at=now,
            )
        )
        uow.leases.save(
            Lease(
                task_id="rs-1",
                attempt_id="a1",
                run_id=run.run_id,
                epoch=1,
                claimed_at=now,
                expires_at=now,
            )
        )
        uow.commit()
    backend_a.close()

    # Fresh process reopens the same store and resumes.
    fake_clock.advance(60)
    backend_b = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts", clock=fake_clock)
    service_b = build_test_service(backend_b)
    await service_b.drive_run(run.run_id)
    with backend_b.unit_of_work() as uow:
        assert uow.tasks.get("rs-1").state is TaskState.SUCCEEDED
    backend_b.close()


# --- 13.8 default tests make no real network calls -------------------------


@pytest.mark.e2e
def test_test_doubles_import_no_network_stack() -> None:
    """The default (offline) test doubles under ``tests/support`` must not import
    httpx/openai/requests anywhere; real adapters lazy-import them inside
    ``invoke`` (13.8)."""

    import ast
    from pathlib import Path

    providers = {"httpx", "openai", "requests", "aiohttp"}
    support_dir = Path(__file__).resolve().parents[1] / "support"
    roots = set()
    for src_file in sorted(support_dir.glob("*.py")):
        module = ast.parse(src_file.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    assert not (roots & providers)

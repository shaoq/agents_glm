"""End-to-end ANALYZE sufficiency closed loop (tasks 10.1 / 10.2 / 10.3)."""

from __future__ import annotations

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.coordination import AdvanceDisposition
from agents_orchestration.domain.enums import (
    CapabilityKind,
    RunState,
    SufficiencyVerdict,
    TaskState,
    TerminationReason,
)
from agents_orchestration.domain.evidence import Evidence, EvidenceSet, SourceIdentity, SourceKind
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.analysis_artifact import (
    SqliteAnalysisArtifactStore,
    accepted_analysis_ref,
)
from agents_orchestration.orchestration.composition import build_production_coordinator
from agents_orchestration.orchestration.focused_replan import FocusedReplanBuilder
from agents_orchestration.orchestration.sufficiency import SufficiencyReview, source_evidence_hash
from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from tests.support.deterministic import (
    FakeAnalyst,
    FakeExecutor,
    FakeGoalNormalizer,
    FakePlanner,
    FakeReviewer,
    FakeSufficiencyReviewer,
    FakeWriter,
    deliverables_provider,
    report_provider,
    research_evidence,
)


def _backend(tmp_path, fake_clock) -> SqliteBackend:
    return SqliteBackend(tmp_path / "runtime.sqlite", tmp_path / "artifacts", clock=fake_clock)


class _GapThenSufficient:
    """RESEARCH_GAP on the first ANALYZE, SUFFICIENT afterwards (10.1 loop)."""

    def __init__(self) -> None:
        self.calls = 0

    async def review(self, run_id, analysis, evidence):
        from agents_orchestration.domain.enums import ReviewSource

        self.calls += 1
        if self.calls == 1:
            return SufficiencyReview(
                verdict=SufficiencyVerdict.RESEARCH_GAP,
                source=ReviewSource.SEMANTIC,
                rationale="missing coverage",
                gap_hint="need competitor pricing",
            )
        return SufficiencyReview(
            verdict=SufficiencyVerdict.SUFFICIENT,
            source=ReviewSource.SEMANTIC,
            rationale="covered",
        )


def _evidence_by_plan(backend):
    async def evidence_set(run_id: str) -> EvidenceSet:
        with backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            plan_version = run.current_plan_version if run is not None else 1
        if plan_version <= 1:
            return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=False)
        return EvidenceSet.join(
            run_id=run_id,
            task_id="research",
            evidences=(
                Evidence(
                    evidence_id="ev-pricing",
                    source=SourceIdentity(source_id="web:pricing", source_kind=SourceKind.WEB),
                    content_text="pricing captured",
                ),
            ),
            required=False,
        )

    return evidence_set


def _service_with_closed_loop(backend) -> OrchestrationService:
    reviewer = _GapThenSufficient()
    artifact_store = SqliteAnalysisArtifactStore(
        SqliteArtifactStore(backend.conn, backend.artifact_dir)
    )
    evidence_set = _evidence_by_plan(backend)
    coordinator = build_production_coordinator(
        backend,
        executor=FakeExecutor(),
        normalizer=FakeGoalNormalizer(),
        planner=FakePlanner(),
        analyst=FakeAnalyst(),
        writer=FakeWriter(),
        reviewer=FakeReviewer(),
        research_evidence=research_evidence,
        evidence_set=evidence_set,
        analysis_provider=_accepted_analysis_provider(backend, artifact_store),
        report_provider=report_provider,
        deliverables_provider=deliverables_provider,
        allowed_capabilities=frozenset(CapabilityKind),
        analysis_artifact_store=artifact_store,
        sufficiency_reviewer=reviewer,
        focused_replan_builder=FocusedReplanBuilder(frozenset(CapabilityKind), backend.idgen),
        limits=SystemLimits(),
    )
    return OrchestrationService(backend, coordinator=coordinator)


def _accepted_analysis_provider(backend, store):
    async def analysis_provider(run_id: str):
        with backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            ref = accepted_analysis_ref(uow.stages, run_id, run.current_plan_version)
            uow.commit()
        return await store.load(ref)

    return analysis_provider


@pytest.mark.e2e
async def test_analyze_gap_loop_reaches_succeeded(tmp_path, fake_clock) -> None:
    backend = _backend(tmp_path, fake_clock)
    service = _service_with_closed_loop(backend)
    run = service.create_run("study market", request_id="e2e-loop")
    await service.drive_run(run.run_id)
    final = service.get_run(run.run_id)

    assert final.state is RunState.SUCCEEDED
    assert final.replan_count == 1  # one real Plan version change
    assert final.current_plan_version == 2
    assert service._coordinator.handlers  # coordinator wired


@pytest.mark.e2e
async def test_closed_loop_records_new_task_and_accepted_artifact(tmp_path, fake_clock) -> None:
    backend = _backend(tmp_path, fake_clock)
    service = _service_with_closed_loop(backend)
    run = service.create_run("study market", request_id="e2e-loop-2")
    await service.drive_run(run.run_id)

    with backend.unit_of_work() as uow:
        v2_tasks = uow.tasks.by_run(run.run_id, plan_version=2)
        research1 = uow.tasks.get("research-1")
        ref = accepted_analysis_ref(uow.stages, run.run_id, 2)
        # evidence hash differs between plan v1 (empty) and v2 (one evidence)
        ev1 = EvidenceSet.join(run_id=run.run_id, task_id="r", evidences=(), required=False)
        ev2 = EvidenceSet.join(
            run_id=run.run_id,
            task_id="r",
            evidences=(
                Evidence(
                    evidence_id="ev-pricing",
                    source=SourceIdentity(source_id="web:pricing", source_kind=SourceKind.WEB),
                    content_text="pricing captured",
                ),
            ),
            required=False,
        )
        uow.commit()
    assert any(t.state is TaskState.SUCCEEDED for t in v2_tasks)  # new task dispatched+accepted
    assert research1.state is TaskState.SUCCEEDED  # preserved, not replayed
    assert research1.attempt_count == 1  # no noop re-dispatch of the old task
    assert ref is not None  # accepted analysis artifact for plan v2
    assert source_evidence_hash(ev1) != source_evidence_hash(ev2)  # evidence hash truly changed


@pytest.mark.e2e
async def test_l0_zero_evidence_exhausts_budget_and_terminates(tmp_path, fake_clock) -> None:
    """10.3: required research with zero evidence (L0) replans until the shared
    budget is exhausted, then terminates REQUIRED_EVIDENCE_MISSING."""

    backend = _backend(tmp_path, fake_clock)

    async def empty_required(run_id):
        return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=True)

    artifact_store = SqliteAnalysisArtifactStore(
        SqliteArtifactStore(backend.conn, backend.artifact_dir)
    )
    coordinator = build_production_coordinator(
        backend,
        executor=FakeExecutor(),
        normalizer=FakeGoalNormalizer(),
        planner=FakePlanner(),
        analyst=FakeAnalyst(),
        writer=FakeWriter(),
        reviewer=FakeReviewer(),
        research_evidence=research_evidence,
        evidence_set=empty_required,
        analysis_provider=_accepted_analysis_provider(backend, artifact_store),
        report_provider=report_provider,
        deliverables_provider=deliverables_provider,
        allowed_capabilities=frozenset(CapabilityKind),
        analysis_artifact_store=artifact_store,
        sufficiency_reviewer=FakeSufficiencyReviewer(),  # unused: L0 short-circuits
        focused_replan_builder=FocusedReplanBuilder(frozenset(CapabilityKind), backend.idgen),
        limits=SystemLimits(),
    )
    service = OrchestrationService(backend, coordinator=coordinator)
    run = service.create_run("study X", request_id="e2e-l0")
    report = await service.drive_run(run.run_id)
    final = service.get_run(run.run_id)
    assert report.disposition is AdvanceDisposition.TERMINAL
    assert final.state is RunState.FAILED
    assert final.termination is TerminationReason.REQUIRED_EVIDENCE_MISSING

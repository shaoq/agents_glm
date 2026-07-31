"""End-to-end adaptive research through the production composition root."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.capabilities.registry import CapabilityRegistry
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.enums import (
    CapabilityKind,
    ReviewSource,
    RunState,
    SufficiencyVerdict,
    WorkerRole,
)
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.plan import (
    ExplorationBoundary,
    ResearchExecutionMode,
    SeedExplorationBoundary,
    TaskSpec,
)
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.domain.research_loop import (
    AddDirectionAction,
    QueryAction,
    ResearchAgentDecision,
    ResearchLoopStatus,
    StopRequestAction,
)
from agents_orchestration.orchestration.analysis_artifact import (
    SqliteAnalysisArtifactStore,
    accepted_analysis_ref,
)
from agents_orchestration.orchestration.composition import build_production_coordinator
from agents_orchestration.orchestration.focused_replan import FocusedReplanBuilder
from agents_orchestration.orchestration.proposals import PlanProposal
from agents_orchestration.orchestration.research_agent_loop import (
    ActionValidator,
    ResearchAgentLoopExecutor,
    ResearchDirectionPolicy,
)
from agents_orchestration.orchestration.sufficiency import SufficiencyReview
from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from agents_orchestration.workers.registry import WorkerRegistry
from tests.support.deterministic import (
    FakeAnalyst,
    FakeExecutor,
    FakeGoalNormalizer,
    FakeReviewer,
    FakeSufficiencyReviewer,
    FakeWriter,
    deliverables_provider,
    report_provider,
)
from tests.support.multi_source_doubles import fake_rag_adapter


class _AgentLoopPlanner:
    def __init__(self, *, max_steps: int = 6) -> None:
        self.max_steps = max_steps

    async def propose_plan(self, goal, completion, run_id: str) -> PlanProposal:
        spec = TaskSpec(
            task_id="seed-1",
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            description="investigate the primary claim",
            required_capabilities=(CapabilityKind.RAG_SEARCH,),
        )
        return PlanProposal(
            run_id=run_id,
            plan_id="adaptive-plan",
            research_execution_mode=ResearchExecutionMode.AGENT_LOOP,
            exploration_boundary=ExplorationBoundary(
                allowed_capabilities=(CapabilityKind.RAG_SEARCH,),
                seeds=(
                    SeedExplorationBoundary(
                        task_id=spec.task_id,
                        required_coverage=(CapabilityKind.RAG_SEARCH,),
                        max_steps=self.max_steps,
                        max_directions=3,
                        max_tokens=2_000,
                    ),
                ),
            ),
            task_specs=(spec,),
            deliverable_paths=("report.md",),
        )


class _ScriptedResearchAgent:
    def __init__(self, actions: list[Callable]) -> None:
        self.actions = list(actions)
        self.views = []

    async def decide(self, view, *, decision_request_id: str) -> ResearchAgentDecision:
        self.views.append(view)
        return ResearchAgentDecision(action=self.actions.pop(0)(view))


class _GapThenSufficient:
    def __init__(self) -> None:
        self.calls = 0

    async def review(self, run_id, analysis, evidence) -> SufficiencyReview:
        self.calls += 1
        if self.calls == 1:
            return SufficiencyReview(
                verdict=SufficiencyVerdict.RESEARCH_GAP,
                source=ReviewSource.SEMANTIC,
                rationale="a focused follow-up is required",
                gap_hint="validate the newly discovered direction",
            )
        return SufficiencyReview(
            verdict=SufficiencyVerdict.SUFFICIENT,
            source=ReviewSource.SEMANTIC,
            rationale="the follow-up closes the gap",
        )


def _query(view):
    return QueryAction(
        direction_id=view.directions[-1].direction_id,
        capability_kind=CapabilityKind.RAG_SEARCH,
        query="collect bounded supporting evidence",
        rationale="cover the approved source",
    )


def _add_direction(view):
    return AddDirectionAction(
        parent_direction_id=view.directions[0].direction_id,
        hint="follow the new direction revealed by Evidence A",
        rationale="Evidence A exposes a bounded follow-up",
    )


def _stop(view):
    return StopRequestAction(
        reason="approved coverage is complete",
        supporting_evidence_ids=tuple(item.evidence_id for item in view.evidence),
    )


def _backend(tmp_path, fake_clock) -> SqliteBackend:
    return SqliteBackend(
        tmp_path / "runtime.sqlite",
        tmp_path / "artifacts",
        clock=fake_clock,
    )


def _service(backend, agent, *, max_steps=6, sufficiency_reviewer=None):
    registry = CapabilityRegistry()
    rag = fake_rag_adapter()
    registry.register(rag.descriptor, rag)
    router = CapabilityRouter(registry, backend.idgen)
    agent_loop_executor = ResearchAgentLoopExecutor(
        agent=agent,
        validator=ActionValidator(),
        direction_policy=ResearchDirectionPolicy(registry.allowed_kinds()),
        registry=registry,
        router=router,
        worker=WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER),
    )
    artifact_store = SqliteAnalysisArtifactStore(
        SqliteArtifactStore(backend.conn, backend.artifact_dir)
    )

    async def research_evidence(run_id: str):
        with backend.unit_of_work() as uow:
            return tuple(uow.evidence.by_run(run_id))

    async def evidence_set(run_id: str) -> EvidenceSet:
        return EvidenceSet.join(
            run_id=run_id,
            task_id="research",
            evidences=await research_evidence(run_id),
            required=True,
        )

    async def analysis_provider(run_id: str):
        with backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            ref = accepted_analysis_ref(
                uow.stages,
                run_id,
                run.current_plan_version,
            )
            uow.commit()
        return await artifact_store.load(ref)

    coordinator = build_production_coordinator(
        backend,
        executor=FakeExecutor(),
        normalizer=FakeGoalNormalizer(),
        planner=_AgentLoopPlanner(max_steps=max_steps),
        analyst=FakeAnalyst(),
        writer=FakeWriter(),
        reviewer=FakeReviewer(),
        research_evidence=research_evidence,
        evidence_set=evidence_set,
        analysis_provider=analysis_provider,
        report_provider=report_provider,
        deliverables_provider=deliverables_provider,
        allowed_capabilities=registry.allowed_kinds(),
        analysis_artifact_store=artifact_store,
        sufficiency_reviewer=sufficiency_reviewer or FakeSufficiencyReviewer(),
        focused_replan_builder=FocusedReplanBuilder(
            registry.allowed_kinds(),
            backend.idgen,
        ),
        agent_loop_executor=agent_loop_executor,
        reasoning_reservation_tokens=1,
        limits=SystemLimits(),
    )
    return OrchestrationService(backend, coordinator=coordinator)


@pytest.mark.e2e
async def test_loop_local_direction_completes_full_lifecycle(
    tmp_path,
    fake_clock,
) -> None:
    backend = _backend(tmp_path, fake_clock)
    agent = _ScriptedResearchAgent([_query, _add_direction, _query, _stop])
    service = _service(backend, agent)
    run = service.create_run("research an adaptive claim", request_id="agent-e2e-1")

    await service.drive_run(run.run_id)

    final = service.get_run(run.run_id)
    with backend.unit_of_work() as uow:
        loop = uow.research_loops.for_task(run.run_id, 1, "seed-1")
        directions = uow.research_directions.by_loop(loop.loop_id)
        evidence = uow.evidence.by_run(run.run_id)
        steps = uow.research_steps.by_loop(loop.loop_id)
    assert final.state is RunState.SUCCEEDED
    assert final.current_plan_version == 1
    assert final.replan_count == 0
    assert loop.status is ResearchLoopStatus.COMPLETED
    assert len(directions) == 2
    assert len(evidence) == 2
    assert len(steps) == 4


@pytest.mark.e2e
async def test_early_stop_then_exhaustion_replans_and_completes_second_round(
    tmp_path,
    fake_clock,
) -> None:
    backend = _backend(tmp_path, fake_clock)
    agent = _ScriptedResearchAgent([_stop, _query, _query, _stop])
    service = _service(
        backend,
        agent,
        max_steps=2,
        sufficiency_reviewer=_GapThenSufficient(),
    )
    run = service.create_run("research then close a gap", request_id="agent-e2e-2")

    await service.drive_run(run.run_id)

    final = service.get_run(run.run_id)
    with backend.unit_of_work() as uow:
        loops_v1 = uow.research_loops.by_run(run.run_id, 1)
        loops_v2 = uow.research_loops.by_run(run.run_id, 2)
        events = tuple(uow.events.stream(run.run_id))
    assert final.state is RunState.SUCCEEDED
    assert final.current_plan_version == 2
    assert final.replan_count == 1
    assert loops_v1[0].status is ResearchLoopStatus.EXHAUSTED
    assert any(loop.status is ResearchLoopStatus.COMPLETED for loop in loops_v2)
    assert any(event.effect.value == "research_stop_rejected" for event in events)

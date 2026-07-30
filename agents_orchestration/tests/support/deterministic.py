"""Deterministic phase-port doubles + a coordinator builder for tests.

These were previously the offline composition living in production code
(``orchestration/composition.py``); they now live under ``tests/`` and feed the
real ``build_production_coordinator`` explicit-port seam. Tests therefore drive
the true production assembly root with deterministic ports instead of a parallel
fake assembly.
"""

from __future__ import annotations

from agents_orchestration.domain.enums import CapabilityKind, ReviewVerdict, WorkerRole
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.composition import build_production_coordinator
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.proposals import (
    GoalNormalizationOutcome,
    PlanProposal,
)
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    ReportContent,
    ReviewProposal,
)
from agents_orchestration.runtime.tick import TaskExecutionOutcome

_DELIVERABLE = "report.md"


def _deliverable_contract(raw_goal: str) -> CompletionContract:
    return CompletionContract(
        criteria=(
            CompletionCriterion(
                kind=CriterionKind.DELIVERABLE,
                description=_DELIVERABLE,
                deliverable_path=_DELIVERABLE,
            ),
        ),
        deliverable_paths=(_DELIVERABLE,),
    )


class FakeGoalNormalizer:
    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
        return GoalNormalizationOutcome(
            GoalSpec(
                raw_input=raw_goal, objective=raw_goal or "research", deliverables=(_DELIVERABLE,)
            ),
            _deliverable_contract(raw_goal),
            None,
        )


class FakePlanner:
    """Proposes research-only Tasks (remove-noop-phase-tasks 6.5): analysis,
    writing, and review run as coordinator-owned phase ports, not as
    dispatchable Tasks, so the deterministic plan contains only research work."""

    async def propose_plan(self, goal, completion, run_id: str) -> PlanProposal:
        specs = (
            TaskSpec(
                task_id="research-1",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gather evidence",
            ),
        )
        return PlanProposal(
            run_id=run_id,
            plan_id="p1",
            task_specs=specs,
            deliverable_paths=(_DELIVERABLE,),
        )


class FakeExecutor:
    async def execute(self, task, attempt, run):
        return TaskExecutionOutcome(succeeded=True)


class FakeAnalyst:
    async def __call__(self, run_id: str, evidence):
        return AnalysisArtifact(run_id=run_id, conclusions=("c1",))


class FakeWriter:
    async def __call__(self, run_id: str, analysis):
        return ReportContent(run_id=run_id, title="Report", objective="objective")


class FakeReviewer:
    async def __call__(self, run_id: str, report):
        return ReviewProposal(verdict=ReviewVerdict.PASS, reason="ok")


async def research_evidence(run_id: str):
    """Raw accepted Evidence tuple for the Research Join."""

    return ()


async def evidence_set(run_id: str) -> EvidenceSet:
    """The joined EvidenceSet for Analysis / Finalize (already-joined view)."""

    return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=False)


async def analysis_provider(run_id: str) -> AnalysisArtifact:
    return AnalysisArtifact(run_id=run_id)


async def report_provider(run_id: str) -> ReportContent:
    return ReportContent(run_id=run_id, title="Report", objective="objective")


async def deliverables_provider(run_id: str) -> dict[str, bool]:
    return {_DELIVERABLE: True}


def build_deterministic_coordinator(backend) -> RunCoordinator:
    """Deterministic coordinator for tests: feeds the real production
    composition root (``build_production_coordinator``) with stub phase ports,
    so a full Goal -> artifacts lifecycle runs without any live provider."""

    return build_production_coordinator(
        backend,
        executor=FakeExecutor(),
        normalizer=FakeGoalNormalizer(),
        planner=FakePlanner(),
        analyst=FakeAnalyst(),
        writer=FakeWriter(),
        reviewer=FakeReviewer(),
        research_evidence=research_evidence,
        evidence_set=evidence_set,
        analysis_provider=analysis_provider,
        report_provider=report_provider,
        deliverables_provider=deliverables_provider,
        allowed_capabilities=frozenset(CapabilityKind),
        limits=SystemLimits(),
    )

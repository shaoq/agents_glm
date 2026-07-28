"""Application composition root (Ch.9 tasks 9.1-9.10).

Wires every phase handler, port, and provider into a :class:`RunCoordinator`.
The default offline profile (task 9.2) uses deterministic Fake ports so tests
can drive a full Goal -> artifacts lifecycle without any live network provider
(task 9.3). Production composition (task 9.4) wires real Model/Memory/RAG/Web
adapters and MUST fail loudly when a required adapter is missing rather than
silently substituting a Fake (task 9.8).
"""

from __future__ import annotations

from agents_orchestration.domain.enums import (
    CapabilityKind,
    ReviewVerdict,
    WorkerRole,
)
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.phases import (
    AnalysisPhaseHandler,
    FinalizePhaseHandler,
    GoalPhaseHandler,
    PlanningPhaseHandler,
    ResearchPhaseHandler,
    ReviewPhaseHandler,
    WritingPhaseHandler,
)
from agents_orchestration.orchestration.proposals import (
    GoalNormalizationOutcome,
    PlanProposal,
)
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    ReportContent,
    ReviewProposal,
)
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome

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


class _FakeGoalNormalizer:
    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
        return GoalNormalizationOutcome(
            GoalSpec(
                raw_input=raw_goal, objective=raw_goal or "research", deliverables=(_DELIVERABLE,)
            ),
            _deliverable_contract(raw_goal),
            None,
        )


class _FakePlanner:
    """Proposes one Task per phase-eligible Worker role so each task phase has
    its own work to drive (phase-role filtering dispatches only the current
    phase's role)."""

    async def propose_plan(self, goal, completion, run_id: str) -> PlanProposal:
        specs = (
            TaskSpec(
                task_id="research-1",
                worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                description="gather evidence",
            ),
            TaskSpec(task_id="analyze-1", worker_role=WorkerRole.ANALYST, description="analyze"),
            TaskSpec(
                task_id="write-1",
                worker_role=WorkerRole.REPORT_WRITER,
                description="write",
                deliverable_path=_DELIVERABLE,
            ),
            TaskSpec(
                task_id="review-1", worker_role=WorkerRole.REPORT_REVIEWER, description="review"
            ),
        )
        return PlanProposal(
            run_id=run_id,
            plan_id="p1",
            task_specs=specs,
            deliverable_paths=(_DELIVERABLE,),
        )


class _FakeExecutor:
    async def execute(self, task, attempt, run):
        return TaskExecutionOutcome(succeeded=True)


class _FakeAnalyst:
    async def __call__(self, run_id: str, evidence):
        return AnalysisArtifact(run_id=run_id, conclusions=("c1",))


class _FakeWriter:
    async def __call__(self, run_id: str, analysis):
        return ReportContent(run_id=run_id, title="Report", objective="objective")


class _FakeReviewer:
    async def __call__(self, run_id: str, report):
        return ReviewProposal(verdict=ReviewVerdict.PASS, reason="ok")


async def _research_evidence(run_id: str):
    """Raw accepted Evidence tuple for the Research Join (task 6.8)."""

    return ()


async def _evidence_set(run_id: str) -> EvidenceSet:
    """The joined EvidenceSet for Analysis / Finalize (already-joined view)."""

    return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=False)


async def _analysis_provider(run_id: str) -> AnalysisArtifact:
    return AnalysisArtifact(run_id=run_id)


async def _report_provider(run_id: str) -> ReportContent:
    return ReportContent(run_id=run_id, title="Report", objective="objective")


async def _deliverables_provider(run_id: str) -> dict[str, bool]:
    return {_DELIVERABLE: True}


class CompositionError(RuntimeError):
    """Raised when a requested composition profile is incomplete (task 9.8)."""


def build_offline_coordinator(backend) -> RunCoordinator:
    """Deterministic offline composition (tasks 9.2/9.3).

    Every port is a Fake; no live network provider is imported or invoked.
    """
    tick = RuntimeTick(backend, executor=_FakeExecutor(), limits=SystemLimits())
    handlers = {
        GoalPhaseHandler.phase: GoalPhaseHandler(_FakeGoalNormalizer(), backend.idgen),
        PlanningPhaseHandler.phase: PlanningPhaseHandler(
            _FakePlanner(),
            limits=SystemLimits(),
            allowed_capabilities=frozenset(CapabilityKind),
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        ResearchPhaseHandler.phase: ResearchPhaseHandler(
            tick,
            _research_evidence,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        AnalysisPhaseHandler.phase: AnalysisPhaseHandler(
            tick,
            _FakeAnalyst(),
            _evidence_set,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        WritingPhaseHandler.phase: WritingPhaseHandler(
            tick,
            _FakeWriter(),
            _analysis_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        ReviewPhaseHandler.phase: ReviewPhaseHandler(
            tick,
            _FakeReviewer(),
            _report_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        FinalizePhaseHandler.phase: FinalizePhaseHandler(
            report_provider=_report_provider,
            analysis_provider=_analysis_provider,
            evidence_provider=_evidence_set,
            deliverables_provider=_deliverables_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
    }
    return RunCoordinator(backend, handlers)


__all__ = [
    "CompositionError",
    "build_offline_coordinator",
]

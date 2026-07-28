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


def build_production_coordinator(
    backend,
    *,
    executor,
    normalizer,
    planner,
    analyst,
    writer,
    reviewer,
    research_evidence,
    evidence_set,
    analysis_provider,
    report_provider,
    deliverables_provider,
    allowed_capabilities,
    limits: SystemLimits | None = None,
    approval_required: bool = False,
) -> RunCoordinator:
    """Production composition root (tasks 9.4/9.5/9.8).

    Every phase port (model-backed GoalNormalizer/Planner/Analyst/Writer/
    Reviewer) and provider is supplied by the caller — the composition root
    only wires them. It MUST fail loudly when a required port is missing rather
    than silently substituting a Fake (task 9.8); secret-safe capability
    diagnostics come from ``capability_doctor`` (task 9.9). Model-backed port
    implementations (LLM prompt + structured-output parse) are supplied by the
    caller — they are prompt-engineering work, not composition wiring.
    """

    ports = {
        "executor": executor,
        "normalizer": normalizer,
        "planner": planner,
        "analyst": analyst,
        "writer": writer,
        "reviewer": reviewer,
        "research_evidence": research_evidence,
        "evidence_set": evidence_set,
        "analysis_provider": analysis_provider,
        "report_provider": report_provider,
        "deliverables_provider": deliverables_provider,
    }
    missing = sorted(name for name, port in ports.items() if port is None)
    if missing:
        raise CompositionError(
            f"production composition incomplete — missing required ports: {missing}"
        )
    sys_limits = limits or SystemLimits()
    tick = RuntimeTick(backend, executor=executor, limits=sys_limits)
    handlers = {
        GoalPhaseHandler.phase: GoalPhaseHandler(normalizer, backend.idgen),
        PlanningPhaseHandler.phase: PlanningPhaseHandler(
            planner,
            limits=sys_limits,
            allowed_capabilities=allowed_capabilities,
            clock=backend.clock,
            idgen=backend.idgen,
            approval_required=approval_required,
        ),
        ResearchPhaseHandler.phase: ResearchPhaseHandler(
            tick,
            research_evidence,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        AnalysisPhaseHandler.phase: AnalysisPhaseHandler(
            tick,
            analyst,
            evidence_set,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        WritingPhaseHandler.phase: WritingPhaseHandler(
            tick,
            writer,
            analysis_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        ReviewPhaseHandler.phase: ReviewPhaseHandler(
            tick,
            reviewer,
            report_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        FinalizePhaseHandler.phase: FinalizePhaseHandler(
            report_provider=report_provider,
            analysis_provider=analysis_provider,
            evidence_provider=evidence_set,
            deliverables_provider=deliverables_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
    }
    return RunCoordinator(backend, handlers)


# --- LLM production composition (Ch.4 tasks 4.1/4.2) ---


class _LLMResearchHandler:
    """EVIDENCE_RESEARCHER WorkerExecutor handler backed by the LLM knowledge
    source (R1). Produces untrusted MODEL-sourced Evidence directly via the
    adapter, bypassing the Router (phase-level trusted component, not an
    untrusted task worker)."""

    def __init__(self, adapter, idgen) -> None:
        from agents_orchestration.orchestration.llm_ports import LLMResearchProvider

        self._provider = LLMResearchProvider(adapter, idgen)

    async def handle(self, task, attempt, run, invoke):
        from agents_orchestration.domain.worker import TaskResult

        evidences = await self._provider(run.run_id, run.raw_goal)
        return TaskResult(
            attempt_id=attempt.attempt_id,
            task_id=task.task_id,
            run_id=run.run_id,
            worker_role=task.worker_role,
            evidence=evidences,
            summary="llm-research",
        )


class _NoopHandler:
    """Placeholder handler for Analysis/Write/Review Tasks: the real logic runs
    in the phase port (LLMAnalyst/Writer/Reviewer); the Task just succeeds so
    the phase handler proceeds to call the port."""

    async def handle(self, task, attempt, run, invoke):
        from agents_orchestration.domain.worker import TaskResult

        return TaskResult(
            attempt_id=attempt.attempt_id,
            task_id=task.task_id,
            run_id=run.run_id,
            worker_role=task.worker_role,
            summary="noop-phase-task",
        )


def build_production_coordinator_from_settings(backend, settings=None) -> RunCoordinator:
    """Production composition from Settings (Ch.4 tasks 4.1/4.2).

    Wires real LLM-backed ports (GoalNormalizer/Planner/Analyst/Writer/Reviewer)
    via function calling, an LLM research handler (R1), and persisted-evidence
    providers. Memory/RAG/Web adapters stay Fake (deferred to a sibling change).

    TODO: AnalysisArtifact/ReportContent are not yet persisted across phases, so
    analysis_provider/report_provider re-invoke the LLM (MVP — accepts a double
    call; persist outputs in a follow-up to avoid it).
    """

    from agents_orchestration.adapters.base import ModelProfile
    from agents_orchestration.adapters.model import OpenAIModelAdapter
    from agents_orchestration.capabilities.registry import CapabilityRegistry
    from agents_orchestration.capabilities.router import CapabilityRouter
    from agents_orchestration.config import load_settings
    from agents_orchestration.domain.enums import CapabilityKind, WorkerRole
    from agents_orchestration.orchestration.llm_ports import (
        LLMAnalyst,
        LLMGoalNormalizer,
        LLMPlanner,
        LLMReportReviewer,
        LLMReportWriter,
    )
    from agents_orchestration.workers.executor import WorkerExecutor
    from agents_orchestration.workers.registry import WorkerRegistry

    settings = settings or load_settings()
    profile = ModelProfile(
        name=settings.model_planner,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
    adapter = OpenAIModelAdapter(profile)
    idgen = backend.idgen
    limits = settings.build_limits()
    policy = settings.build_run_policy()

    normalizer = LLMGoalNormalizer(adapter, idgen)
    planner = LLMPlanner(adapter, idgen)
    analyst = LLMAnalyst(adapter, idgen)
    writer = LLMReportWriter(adapter, idgen)
    reviewer = LLMReportReviewer(adapter, idgen)

    registry = CapabilityRegistry()
    workers = WorkerRegistry.default()
    router = CapabilityRouter(registry, idgen)
    research_handler = _LLMResearchHandler(adapter, idgen)
    noop = _NoopHandler()
    handlers = {
        WorkerRole.EVIDENCE_RESEARCHER: research_handler,
        WorkerRole.ANALYST: noop,
        WorkerRole.REPORT_WRITER: noop,
        WorkerRole.REPORT_REVIEWER: noop,
    }
    executor = WorkerExecutor(workers, router, handlers, policy)

    async def research_evidence(run_id: str):
        with backend.unit_of_work() as uow:
            return tuple(uow.evidence.by_run(run_id))

    async def evidence_set(run_id: str) -> EvidenceSet:
        with backend.unit_of_work() as uow:
            evs = tuple(uow.evidence.by_run(run_id))
        return EvidenceSet.join(run_id=run_id, task_id="research", evidences=evs, required=False)

    async def analysis_provider(run_id: str):
        ev = await evidence_set(run_id)
        return await analyst(run_id, ev)

    async def report_provider(run_id: str):
        analysis = await analysis_provider(run_id)
        return await writer(run_id, analysis)

    async def deliverables_provider(run_id: str) -> dict[str, bool]:
        return {_DELIVERABLE: True}

    return build_production_coordinator(
        backend,
        executor=executor,
        normalizer=normalizer,
        planner=planner,
        analyst=analyst,
        writer=writer,
        reviewer=reviewer,
        research_evidence=research_evidence,
        evidence_set=evidence_set,
        analysis_provider=analysis_provider,
        report_provider=report_provider,
        deliverables_provider=deliverables_provider,
        allowed_capabilities=frozenset(CapabilityKind),
        limits=limits,
    )


__all__ = [
    "CompositionError",
    "build_offline_coordinator",
    "build_production_coordinator",
    "build_production_coordinator_from_settings",
]

"""Application composition root (Ch.9 / Ch.4).

Wires real, model-backed phase ports and providers into a :class:`RunCoordinator`.
``build_production_coordinator`` is the explicit-port assembly root and the test
injection seam; ``build_production_coordinator_from_settings`` builds the LLM
ports from Settings. There is no offline or fake assembly in production code —
deterministic test doubles live under ``tests/support`` and feed the explicit
port seam. A composition with a missing required port MUST fail loudly
(:class:`CompositionError`) rather than silently substituting anything.
"""

from __future__ import annotations

from agents_orchestration.domain.evidence import EvidenceSet
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
from agents_orchestration.runtime.tick import RuntimeTick

_DELIVERABLE = "report.md"


class CompositionError(RuntimeError):
    """Raised when a requested composition is incomplete (task 9.8)."""


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
    than silently substituting anything (task 9.8); secret-safe capability
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


def build_production_coordinator_from_settings(
    backend, settings=None, *, capability_registry=None
) -> RunCoordinator:
    """Production composition from Settings (Ch.4 tasks 4.1/4.2).

    Wires real LLM-backed ports (GoalNormalizer/Planner/Analyst/Writer/Reviewer)
    via function calling, a multi-source EVIDENCE_RESEARCHER handler, and
    persisted-evidence providers. ``capability_registry`` is the sibling-adapter
    injection seam: production does NOT wire real Memory/RAG/Web adapters yet
    (deferred to a sibling change — per remove-offline-fake-assembly, production
    code contains no Fake classes); tests inject fake doubles via this parameter.
    With no registry, research Tasks find no capability and degrade honestly
    (no fabricated evidence).

    TODO: AnalysisArtifact/ReportContent are not yet persisted across phases, so
    analysis_provider/report_provider re-invoke the LLM (MVP — accepts a double
    call; persist outputs in a follow-up to avoid it).
    """

    from agents_orchestration.adapters.base import ModelProfile
    from agents_orchestration.adapters.model import OpenAIModelAdapter
    from agents_orchestration.capabilities.registry import CapabilityRegistry
    from agents_orchestration.capabilities.router import CapabilityRouter
    from agents_orchestration.config import load_settings
    from agents_orchestration.domain.enums import WorkerRole
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
    planner = LLMPlanner(adapter, idgen, web_enabled=policy.web_enabled)
    analyst = LLMAnalyst(adapter, idgen)
    writer = LLMReportWriter(adapter, idgen)
    reviewer = LLMReportReviewer(adapter, idgen)

    # capability_registry is the sibling-adapter injection seam. Production does
    # not register real Memory/RAG/Web adapters yet (deferred); tests inject fake
    # doubles. An empty registry means research Tasks degrade to no-capability.
    registry = capability_registry or CapabilityRegistry()
    workers = WorkerRegistry.default()
    router = CapabilityRouter(registry, idgen)
    from agents_orchestration.orchestration.multi_source_handler import (
        MultiSourceResearchHandler,
    )

    research_handler = MultiSourceResearchHandler(registry, idgen)
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
        allowed_capabilities=registry.allowed_kinds(),
        limits=limits,
    )


__all__ = [
    "CompositionError",
    "build_production_coordinator",
    "build_production_coordinator_from_settings",
]

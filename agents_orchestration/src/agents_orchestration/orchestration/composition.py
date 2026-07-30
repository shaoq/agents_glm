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
from agents_orchestration.orchestration.focused_replan import FocusedReplanBuilder
from agents_orchestration.orchestration.phases import (
    AnalysisPhaseHandler,
    FinalizePhaseHandler,
    GoalPhaseHandler,
    PlanningPhaseHandler,
    ResearchPhaseHandler,
    ReviewPhaseHandler,
    WritingPhaseHandler,
)
from agents_orchestration.orchestration.planner import PlanValidator
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
    analysis_artifact_store=None,
    sufficiency_reviewer=None,
    focused_replan_builder=None,
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

    ``analysis_artifact_store`` defaults to ``None`` and is validated here so a
    missing artifact port raises :class:`CompositionError` (analyze-sufficiency-
    feedback Decision 10 / task 8.1) rather than a ``TypeError`` downstream.
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
        "analysis_artifact_store": analysis_artifact_store,
        "sufficiency_reviewer": sufficiency_reviewer,
        "focused_replan_builder": focused_replan_builder,
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
            analyst,
            evidence_set,
            analysis_artifact_store,
            sufficiency_reviewer,
            focused_replan_builder,
            PlanValidator(sys_limits),
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        WritingPhaseHandler.phase: WritingPhaseHandler(
            writer,
            analysis_provider,
            clock=backend.clock,
            idgen=backend.idgen,
        ),
        ReviewPhaseHandler.phase: ReviewPhaseHandler(
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

    AnalysisArtifact is now persisted by the ANALYZE phase and loaded by the
    WRITING/FINALIZE providers via the accepted-stage handoff
    (analyze-sufficiency-feedback task 3.3); ReportContent is still re-derived
    by report_provider (a follow-up can persist it the same way).
    """

    from agents_orchestration.adapters.base import ModelProfile
    from agents_orchestration.adapters.model import OpenAIModelAdapter
    from agents_orchestration.capabilities.registry import CapabilityRegistry
    from agents_orchestration.capabilities.router import CapabilityRouter
    from agents_orchestration.config import load_settings
    from agents_orchestration.domain.enums import WorkerRole
    from agents_orchestration.orchestration.llm_ports import (
        LLMAnalyst,
        LLMEvidenceSufficiencyReviewer,
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
    sufficiency_reviewer = LLMEvidenceSufficiencyReviewer(adapter, idgen)

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
    focused_replan_builder = FocusedReplanBuilder(registry.allowed_kinds(), idgen)
    handlers = {
        WorkerRole.EVIDENCE_RESEARCHER: research_handler,
    }
    executor = WorkerExecutor(workers, router, handlers, policy)

    from agents_orchestration.orchestration.analysis_artifact import (
        MissingAcceptedAnalysisError,
        SqliteAnalysisArtifactStore,
        accepted_analysis_ref,
    )
    from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore

    analysis_artifact_store = SqliteAnalysisArtifactStore(
        SqliteArtifactStore(backend.conn, backend.artifact_dir)
    )

    async def research_evidence(run_id: str):
        with backend.unit_of_work() as uow:
            return tuple(uow.evidence.by_run(run_id))

    async def evidence_set(run_id: str) -> EvidenceSet:
        with backend.unit_of_work() as uow:
            evs = tuple(uow.evidence.by_run(run_id))
        return EvidenceSet.join(run_id=run_id, task_id="research", evidences=evs, required=False)

    async def analysis_provider(run_id: str):
        # WRITING/FINALIZE load the ANALYZE-accepted artifact; the analyst is no
        # longer re-invoked downstream (analyze-sufficiency-feedback task 3.3).
        with backend.unit_of_work() as uow:
            run = uow.runs.get(run_id)
            plan_version = run.current_plan_version if run is not None else None
            ref = (
                accepted_analysis_ref(uow.stages, run_id, plan_version)
                if plan_version is not None
                else None
            )
            uow.commit()
        if ref is None:
            raise MissingAcceptedAnalysisError(f"no accepted analysis for run {run_id}")
        return await analysis_artifact_store.load(ref)

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
        analysis_artifact_store=analysis_artifact_store,
        sufficiency_reviewer=sufficiency_reviewer,
        focused_replan_builder=focused_replan_builder,
        limits=limits,
    )


__all__ = [
    "CompositionError",
    "build_production_coordinator",
    "build_production_coordinator_from_settings",
]

"""LLM-backed phase ports via function calling (Ch.3 tasks 3.1-3.6).

Each port holds an :class:`OpenAIModelAdapter` (the MODEL capability boundary)
and calls ``invoke_tools`` with a Pydantic-schema tool, parsing the model's
``tool_call.arguments`` into the typed domain model. Parse/provider failures
raise :class:`PortError` so the phase handler degrades to IDLE (task 3.x) rather
than fabricating output. The ReportWriter uses plain-text ``invoke`` for long-form
markdown (design Open Question). These ports are phase-level (called by the
coordinator) and bypass the task Router — they are trusted orchestration
components, not untrusted workers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agents_orchestration.adapters.llm_tools import pydantic_to_tool
from agents_orchestration.adapters.model import OpenAIModelAdapter
from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.enums import (
    BranchRole,
    CapabilityKind,
    FailureCode,
    ReviewVerdict,
    WorkerRole,
)
from agents_orchestration.domain.evidence import Evidence, EvidenceSet, SourceIdentity, SourceKind
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.orchestration.proposals import (
    GoalNormalizationOutcome,
    PlanProposal,
)
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    ReportContent,
    ReportSection,
    ReviewProposal,
)

_DELIVERABLE = "report.md"

# The Planner emits only evidence_researcher Tasks: ``_TaskSpecOut.role`` is a
# Literal, so any other role fails structured-output validation instead of
# falling back to a research Task. ANALYST / REPORT_WRITER / REPORT_REVIEWER are
# coordinator-owned phase ports (LLMAnalyst / LLMReportWriter / LLMReportReviewer),
# not dispatched Tasks, so they have no Planner role mapping here.


# --- Multi-source hint mapping (task 2.3 / 2.4) ---


# Semantic source labels (what the LLM picks) → (CapabilityKind, default BranchRole).
# The LLM never names a CapabilityKind directly; this deterministic map is the
# single place labels become capabilities, behind three guard rails:
# web filter here → PlanValidator (allowed_capabilities) → Router policy.
_SOURCE_HINT_MAP: dict[str, tuple[CapabilityKind, BranchRole]] = {
    "local_knowledge": (CapabilityKind.RAG_SEARCH, BranchRole.REQUIRED),
    "personal_context": (CapabilityKind.MEMORY_RECALL, BranchRole.REQUIRED),
    "live_web": (CapabilityKind.WEB_RESEARCH, BranchRole.OPTIONAL),
}


def map_source_hints(
    hints: list[str],
    *,
    web_enabled: bool,
) -> tuple[tuple[CapabilityKind, BranchRole], ...]:
    """Deterministic mapping: semantic source labels → ``(CapabilityKind, BranchRole)``.

    - ``live_web`` is dropped when ``web_enabled`` is False (config-level filter; the
      lane would be rejected by the Router anyway — dropping avoids a doomed dispatch).
    - Empty ``hints`` falls back to ``[local_knowledge]`` so a research task always
      has at least one source.
    - Unknown labels are ignored; duplicate capabilities are de-duplicated.
    - May return an empty tuple (all hints filtered/unknown) — the caller records a
      Degradation for the resulting no-capability task.
    """

    if not hints:
        hints = ["local_knowledge"]
    out: list[tuple[CapabilityKind, BranchRole]] = []
    seen: set[CapabilityKind] = set()
    for hint in hints:
        if hint == "live_web" and not web_enabled:
            continue
        mapped = _SOURCE_HINT_MAP.get(hint)
        if mapped is None or mapped[0] in seen:
            continue
        seen.add(mapped[0])
        out.append(mapped)
    return tuple(out)


class PortError(RuntimeError):
    """Raised when the model call fails or its output cannot be parsed."""

    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class _LLMPortBase:
    """Shared function-calling helper for LLM-backed phase ports."""

    def __init__(self, adapter: OpenAIModelAdapter, idgen) -> None:
        self.adapter = adapter
        self.idgen = idgen

    async def _call_tool(
        self,
        prompt: str,
        model_cls: type[BaseModel],
        tool_name: str,
        tool_description: str,
    ) -> BaseModel:
        request = CapabilityRequest(
            request_id=self.idgen.new_id("llm"),
            capability_id=self.adapter.descriptor.capability_id,
            worker_id=f"port::{tool_name}",
            run_id="port",
            task_id="port",
            attempt_id="port",
            inputs={"prompt": prompt},
        )
        tool = pydantic_to_tool(model_cls, name=tool_name, description=tool_description)
        result = await self.adapter.invoke_tools(request, [tool])
        if not result.succeeded:
            raise PortError(
                result.failure_code or FailureCode.UPSTREAM_ERROR,
                f"{tool_name} model call failed",
            )
        try:
            return model_cls.model_validate_json(result.data["arguments"])
        except Exception as exc:  # noqa: BLE001 - degrade on any parse failure
            raise PortError(
                FailureCode.INVALID_RESPONSE, f"{tool_name} parse failed: {exc}"
            ) from None

    async def _call_text(self, prompt: str) -> str:
        request = CapabilityRequest(
            request_id=self.idgen.new_id("llm"),
            capability_id=self.adapter.descriptor.capability_id,
            worker_id="port::writer",
            run_id="port",
            task_id="port",
            attempt_id="port",
            inputs={"prompt": prompt},
        )
        result = await self.adapter.invoke(request)
        if not result.succeeded:
            raise PortError(
                result.failure_code or FailureCode.UPSTREAM_ERROR, "writer model call failed"
            )
        return str(result.data.get("text", ""))


# --- Goal (3.1) ---


class _GoalOutput(BaseModel):
    objective: str
    scope: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=lambda: [_DELIVERABLE])
    constraints: list[str] = Field(default_factory=list)


class LLMGoalNormalizer(_LLMPortBase):
    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome:
        prompt = (
            "You are a research-goal normalizer. Given a raw research goal, produce a clear "
            "objective (one research question), scope (aspects to investigate), deliverables "
            "(output file names, default report.md), and constraints. "
            f"Raw goal: {raw_goal}"
        )
        out = await self._call_tool(
            prompt,
            _GoalOutput,
            "normalize_goal",
            "Normalize a research goal into structured fields",
        )
        out_typed: _GoalOutput = out  # type: ignore[assignment]
        deliverables = tuple(out_typed.deliverables) or (_DELIVERABLE,)
        goal = GoalSpec(
            raw_input=raw_goal,
            objective=out_typed.objective,
            scope=tuple(out_typed.scope),
            constraints=tuple(out_typed.constraints),
            deliverables=deliverables,
        )
        contract = CompletionContract(
            criteria=tuple(
                CompletionCriterion(
                    kind=CriterionKind.DELIVERABLE, description=d, deliverable_path=d
                )
                for d in deliverables
            ),
            deliverable_paths=deliverables,
        )
        return GoalNormalizationOutcome(goal, contract, None)


# --- Planner (3.2) ---


class _TaskSpecOut(BaseModel):
    task_id: str
    role: Literal["evidence_researcher"]
    description: str
    deliverable: str | None = None
    source_hints: list[Literal["local_knowledge", "personal_context", "live_web"]] = Field(
        default_factory=list
    )


class _PlanOutput(BaseModel):
    tasks: list[_TaskSpecOut]
    deliverable_paths: list[str] = Field(default_factory=lambda: [_DELIVERABLE])


class LLMPlanner(_LLMPortBase):
    def __init__(self, adapter: OpenAIModelAdapter, idgen, *, web_enabled: bool = False) -> None:
        super().__init__(adapter, idgen)
        self._web_enabled = web_enabled

    async def propose_plan(self, goal: GoalSpec, completion, run_id: str) -> PlanProposal:
        prompt = (
            "You are a research planner. Break the research objective into a few independent "
            "sub-questions and emit one evidence_researcher task per sub-question, each with a "
            "focused description (used as the search query). Every task role MUST be "
            "evidence_researcher — do NOT emit analyst, report_writer, or report_reviewer tasks "
            "(those phases run separately). For each task, pick source_hints from: "
            "local_knowledge (local knowledge base), personal_context (personalized memory), "
            "live_web (real-time web, expensive). Choose the sources that fit that sub-question. "
            "Each task has: task_id (stable id), role (evidence_researcher), description, "
            "optional deliverable, source_hints. "
            f"Research objective: {goal.objective}. Scope: {', '.join(goal.scope) or '(none)'}."
        )
        out = await self._call_tool(
            prompt, _PlanOutput, "propose_plan", "Propose a bounded research plan"
        )
        out_typed: _PlanOutput = out  # type: ignore[assignment]
        specs = []
        for t in out_typed.tasks:
            # _TaskSpecOut.role is Literal["evidence_researcher"]; any other role
            # fails structured-output validation rather than becoming a research Task.
            caps_roles = map_source_hints(t.source_hints, web_enabled=self._web_enabled)
            required_capabilities = tuple(cr[0] for cr in caps_roles)
            specs.append(
                TaskSpec(
                    task_id=t.task_id,
                    worker_role=WorkerRole.EVIDENCE_RESEARCHER,
                    description=t.description,
                    deliverable_path=t.deliverable,
                    required_capabilities=required_capabilities,
                    branch_role=BranchRole.REQUIRED,
                )
            )
        return PlanProposal(
            run_id=run_id,
            plan_id="llm-plan",
            task_specs=tuple(specs),
            deliverable_paths=tuple(out_typed.deliverable_paths) or (_DELIVERABLE,),
        )


# --- Analyst (3.3) ---


class _AnalysisOutput(BaseModel):
    conclusions: list[str]
    cited_evidence_ids: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class LLMAnalyst(_LLMPortBase):
    async def __call__(self, run_id: str, evidence: EvidenceSet) -> AnalysisArtifact:
        digest = self._evidence_digest(evidence)
        prompt = (
            "You are a research analyst. Given the evidence below, produce 2-5 evidence-linked "
            "conclusions, the evidence ids each conclusion rests on, and any open questions. "
            f"Evidence:\n{digest}"
        )
        out = await self._call_tool(
            prompt, _AnalysisOutput, "analyze_evidence", "Analyze evidence into conclusions"
        )
        out_typed: _AnalysisOutput = out  # type: ignore[assignment]
        return AnalysisArtifact(
            run_id=run_id,
            conclusions=tuple(out_typed.conclusions),
            cited_evidence_ids=tuple(out_typed.cited_evidence_ids),
            open_questions=tuple(out_typed.open_questions),
        )

    @staticmethod
    def _evidence_digest(evidence: EvidenceSet) -> str:
        if not evidence.evidences:
            return "(no evidence available)"
        lines = []
        for ev in evidence.evidences:
            lines.append(f"[{ev.evidence_id}] ({ev.source.source_kind.value}) {ev.content_text}")
        return "\n".join(lines)


# --- ReportWriter (3.4) ---


class LLMReportWriter(_LLMPortBase):
    async def __call__(self, run_id: str, analysis: AnalysisArtifact) -> ReportContent:
        conclusions = "; ".join(analysis.conclusions) or "(no conclusions)"
        prompt = (
            "You are a research report writer. Write a concise Markdown research report "
            "fulfilling the objective. Use [evidence_id] inline citations where relevant. "
            f"Conclusions to support: {conclusions}. Cited evidence ids: "
            f"{', '.join(analysis.cited_evidence_ids) or '(none)'}."
        )
        text = await self._call_text(prompt)
        title = "Research Report"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped.lstrip("# ").strip()
                break
        return ReportContent(
            run_id=run_id,
            title=title,
            objective="research report",
            sections=(
                ReportSection(
                    title="Report", body=text, cited_evidence_ids=analysis.cited_evidence_ids
                ),
            ),
            conclusions=analysis.conclusions,
            cited_evidence_ids=analysis.cited_evidence_ids,
        )


# --- Reviewer (3.5) ---


class _ReviewOutput(BaseModel):
    verdict: str
    reason: str
    suggested_actions: list[str] = Field(default_factory=list)


class LLMReportReviewer(_LLMPortBase):
    async def __call__(self, run_id: str, report: ReportContent) -> ReviewProposal:
        body = report.sections[0].body if report.sections else ""
        prompt = (
            "You are a report reviewer. Evaluate the report below. Choose a verdict from: "
            "pass, revise, research_gap, conflict, escalate. Give a reason and suggested actions. "
            f"Report:\n{body[:4000]}"
        )
        out = await self._call_tool(
            prompt, _ReviewOutput, "review_report", "Review a research report"
        )
        out_typed: _ReviewOutput = out  # type: ignore[assignment]
        try:
            verdict = ReviewVerdict(out_typed.verdict.lower().strip())
        except ValueError:
            verdict = ReviewVerdict.ESCALATE
        return ReviewProposal(
            verdict=verdict,
            reason=out_typed.reason,
            suggested_actions=tuple(out_typed.suggested_actions),
        )


# --- Research LLM knowledge source (3.6 / R1) ---


class _ResearchEvidenceOutput(BaseModel):
    """The LLM knowledge-source evidence for the Research phase (R1)."""

    evidence: list[_EvidenceItemOut] = Field(default_factory=list)


class _EvidenceItemOut(BaseModel):
    content: str
    citation: str | None = None
    confidence: float = 0.6


class LLMResearchProvider(_LLMPortBase):
    """R1: produce untrusted MODEL-sourced evidence from the LLM's own knowledge.

    Used by the production evidence_provider so the Research phase has real evidence
    to join/analyze until real Memory/RAG adapters are wired (deferred).
    """

    async def __call__(self, run_id: str, objective: str) -> tuple[Evidence, ...]:
        prompt = (
            "You are a research knowledge source. Given the research objective, list the key "
            "facts/passages you know that are relevant, each with a citation label and your "
            "confidence (0-1). These will be used as research evidence. "
            f"Objective: {objective}"
        )
        out = await self._call_tool(
            prompt,
            _ResearchEvidenceOutput,
            "gather_evidence",
            "Gather evidence relevant to the objective",
        )
        out_typed: _ResearchEvidenceOutput = out  # type: ignore[assignment]
        evidences: list[Evidence] = []
        for idx, item in enumerate(out_typed.evidence):
            eid = f"llm-evidence-{idx + 1}"
            evidences.append(
                Evidence(
                    evidence_id=eid,
                    source=SourceIdentity(
                        source_id="model:glm",
                        source_kind=SourceKind.MODEL,
                        uri=f"llm://{eid}",
                        trust=max(0.0, min(1.0, item.confidence)),
                    ),
                    content_text=item.content,
                    citation=item.citation,
                    trust=max(0.0, min(1.0, item.confidence)),
                    is_untrusted=True,
                )
            )
        return tuple(evidences)


__all__ = [
    "LLMAnalyst",
    "LLMGoalNormalizer",
    "LLMPlanner",
    "LLMReportReviewer",
    "LLMReportWriter",
    "LLMResearchProvider",
    "PortError",
]

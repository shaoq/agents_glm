"""Unit tests for LLM-backed phase ports (Ch.3 tasks 3.1-3.6) with a stubbed adapter."""

from __future__ import annotations

import pytest

from agents_orchestration.adapters.base import ModelProfile
from agents_orchestration.adapters.model import OpenAIModelAdapter
from agents_orchestration.domain.capability import CapabilityResult
from agents_orchestration.domain.enums import (
    BranchRole,
    CapabilityKind,
    FailureCode,
    ReviewVerdict,
    WorkerRole,
)
from agents_orchestration.domain.evidence import EvidenceSet, Usage
from agents_orchestration.domain.plan import ResearchExecutionMode
from agents_orchestration.domain.research_loop import (
    QueryAction,
    ResearchDirectionView,
    ResearchLoopView,
)
from agents_orchestration.orchestration.llm_ports import (
    LLMAnalyst,
    LLMGoalNormalizer,
    LLMPlanner,
    LLMReportReviewer,
    LLMReportWriter,
    LLMResearchAgent,
    LLMResearchProvider,
    PortError,
)
from agents_orchestration.orchestration.report import AnalysisArtifact


class _Id:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_1"


class _FakeAdapter(OpenAIModelAdapter):
    """Overrides invoke_tools/invoke to return a canned result (no network)."""

    def __init__(self, *, tool_result=None, text=None) -> None:
        super().__init__(ModelProfile(name="m", base_url="u", api_key="k"))
        self._tool_result = tool_result
        self._text = text

    async def invoke_tools(self, request, tools):  # type: ignore[override]
        if isinstance(self._tool_result, Exception):
            raise self._tool_result
        return self._tool_result

    async def invoke(self, request):  # type: ignore[override]
        if isinstance(self._text, Exception):
            raise self._text
        return CapabilityResult.ok(operation_id="op", data={"text": self._text or ""})


def _tool_ok(arguments: str, name: str = "tool") -> CapabilityResult:
    return CapabilityResult.ok(operation_id="op", data={"tool_name": name, "arguments": arguments})


@pytest.mark.unit
async def test_goal_normalizer_parses_and_builds_contract() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"objective":"研究X","scope":["a","b"],"deliverables":["report.md"]}',
            "normalize_goal",
        )
    )
    out = await LLMGoalNormalizer(adapter, _Id()).normalize("研究 X", "r1")
    assert out.goal.objective == "研究X"
    assert out.goal.scope == ("a", "b")
    assert out.completion.deliverable_paths == ("report.md",)
    assert out.clarification is None


@pytest.mark.unit
async def test_planner_parses_task_specs() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"tasks":['
            '{"task_id":"t1","role":"evidence_researcher","description":"gather A",'
            '"source_hints":["local_knowledge"]},'
            '{"task_id":"t2","role":"evidence_researcher","description":"gather B",'
            '"source_hints":["live_web"]}'
            '],"deliverable_paths":["report.md"]}',
            "propose_plan",
        )
    )
    from agents_orchestration.domain.goal import GoalSpec

    proposal = await LLMPlanner(adapter, _Id()).propose_plan(
        GoalSpec(raw_input="g", objective="研究X", deliverables=("report.md",)),
        None,
        "r1",
    )
    assert len(proposal.task_specs) == 2
    assert all(s.worker_role is WorkerRole.EVIDENCE_RESEARCHER for s in proposal.task_specs)
    assert proposal.deliverable_paths == ("report.md",)


@pytest.mark.unit
async def test_planner_agent_loop_emits_boundary_from_seed_hints() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"tasks":[{"task_id":"seed-1","role":"evidence_researcher",'
            '"description":"gather A","source_hints":["local_knowledge"]}]}',
            "propose_plan",
        )
    )
    from agents_orchestration.domain.goal import GoalSpec

    proposal = await LLMPlanner(
        adapter,
        _Id(),
        default_execution_mode=ResearchExecutionMode.AGENT_LOOP,
        max_steps_per_seed=5,
        max_directions_per_seed=2,
        max_tokens_per_seed=1_000,
    ).propose_plan(
        GoalSpec(raw_input="g", objective="研究X", deliverables=("report.md",)),
        None,
        "r1",
    )

    assert proposal.research_execution_mode is ResearchExecutionMode.AGENT_LOOP
    assert proposal.exploration_boundary is not None
    seed = proposal.exploration_boundary.for_seed("seed-1")
    assert seed.required_coverage == (CapabilityKind.RAG_SEARCH,)
    assert seed.max_steps == 5


@pytest.mark.unit
async def test_planner_agent_loop_keeps_optional_sources_out_of_required_coverage() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"tasks":[{"task_id":"seed-1","role":"evidence_researcher",'
            '"description":"gather A",'
            '"source_hints":["local_knowledge","live_web"]}]}',
            "propose_plan",
        )
    )
    from agents_orchestration.domain.goal import GoalSpec

    proposal = await LLMPlanner(
        adapter,
        _Id(),
        web_enabled=True,
        default_execution_mode=ResearchExecutionMode.AGENT_LOOP,
    ).propose_plan(
        GoalSpec(raw_input="g", objective="研究X", deliverables=("report.md",)),
        None,
        "r1",
    )

    assert proposal.exploration_boundary is not None
    assert proposal.exploration_boundary.allowed_capabilities == (
        CapabilityKind.RAG_SEARCH,
        CapabilityKind.WEB_RESEARCH,
    )
    seed = proposal.exploration_boundary.for_seed("seed-1")
    assert seed.required_coverage == (CapabilityKind.RAG_SEARCH,)
    assert proposal.task_specs[0].required_capabilities == (CapabilityKind.RAG_SEARCH,)
    assert proposal.task_specs[0].branch_role is BranchRole.REQUIRED


@pytest.mark.unit
async def test_planner_rejects_non_research_role() -> None:
    """A non-evidence_researcher role fails structured-output validation rather
    than falling back to a research Task (remove-noop-phase-tasks 1.2)."""

    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"tasks":[{"task_id":"t1","role":"report_writer","description":"write"}]}',
            "propose_plan",
        )
    )
    from agents_orchestration.domain.goal import GoalSpec

    with pytest.raises(PortError) as exc_info:
        await LLMPlanner(adapter, _Id()).propose_plan(
            GoalSpec(raw_input="g", objective="研究X", deliverables=("report.md",)),
            None,
            "r1",
        )
    assert exc_info.value.code is FailureCode.INVALID_RESPONSE


@pytest.mark.unit
async def test_analyst_parses_conclusions() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"conclusions":["c1","c2"],"cited_evidence_ids":["e1"]}', "analyze_evidence"
        )
    )
    artifact = await LLMAnalyst(adapter, _Id())(
        "r1",
        EvidenceSet(
            run_id="r1",
            evidences=(),
            sufficiency=__import__(
                "agents_orchestration.domain.enums", fromlist=["Sufficiency"]
            ).Sufficiency.SUFFICIENT,
        ),
    )
    assert artifact.conclusions == ("c1", "c2")
    assert artifact.cited_evidence_ids == ("e1",)


@pytest.mark.unit
async def test_reviewer_parses_verdict() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok('{"verdict":"pass","reason":"ok"}', "review_report")
    )
    from agents_orchestration.orchestration.report import ReportContent

    verdict = await LLMReportReviewer(adapter, _Id())(
        "r1", ReportContent(run_id="r1", title="t", objective="o")
    )
    assert verdict.verdict is ReviewVerdict.PASS


@pytest.mark.unit
async def test_writer_uses_plain_text() -> None:
    adapter = _FakeAdapter(text="# My Report\nbody text")
    report = await LLMReportWriter(adapter, _Id())(
        "r1", AnalysisArtifact(run_id="r1", conclusions=("c1",))
    )
    assert report.title == "My Report"
    assert "body text" in report.sections[0].body


@pytest.mark.unit
async def test_research_provider_emits_untrusted_model_evidence() -> None:
    adapter = _FakeAdapter(
        tool_result=_tool_ok(
            '{"evidence":[{"content":"fact A","citation":"c1","confidence":0.8}]}',
            "gather_evidence",
        )
    )
    evidences = await LLMResearchProvider(adapter, _Id())("r1", "objective")
    assert len(evidences) == 1
    assert evidences[0].is_untrusted is True
    assert evidences[0].source.source_kind.value == "model"
    assert evidences[0].trust == 0.8


@pytest.mark.unit
async def test_research_agent_uses_stable_request_and_parses_typed_action() -> None:
    adapter = _FakeAdapter(
        tool_result=CapabilityResult.ok(
            operation_id="op",
            data={
                "arguments": (
                    '{"action":{"kind":"query","direction_id":"dir-1",'
                    '"capability_kind":"rag_search","query":"q","rationale":"r"}}'
                )
            },
            usage=Usage(tokens=17),
        )
    )
    view = ResearchLoopView(
        run_id="r1",
        plan_version=1,
        task_id="t1",
        loop_id="loop-1",
        step_id="step-1",
        objective="o",
        seed="s",
        directions=(
            ResearchDirectionView(
                direction_id="dir-1",
                text="[UNTRUSTED_DIRECTION] s",
                capability_scope=(CapabilityKind.RAG_SEARCH,),
            ),
        ),
        remaining_steps=2,
        remaining_directions=1,
    )

    decision = await LLMResearchAgent(adapter).decide(view, decision_request_id="decision:stable")

    assert isinstance(decision.action, QueryAction)
    assert decision.usage.tokens == 17


@pytest.mark.unit
async def test_port_degrades_on_failed_model_call() -> None:
    adapter = _FakeAdapter(
        tool_result=CapabilityResult.failed(
            operation_id="op", failure_code=FailureCode.UPSTREAM_ERROR, retryable=True
        )
    )
    with pytest.raises(PortError) as exc_info:
        await LLMGoalNormalizer(adapter, _Id()).normalize("g", "r1")
    assert exc_info.value.code is FailureCode.UPSTREAM_ERROR


@pytest.mark.unit
async def test_port_degrades_on_unparseable_arguments() -> None:
    adapter = _FakeAdapter(tool_result=_tool_ok("{not valid json", "normalize_goal"))
    with pytest.raises(PortError) as exc_info:
        await LLMGoalNormalizer(adapter, _Id()).normalize("g", "r1")
    assert exc_info.value.code is FailureCode.INVALID_RESPONSE

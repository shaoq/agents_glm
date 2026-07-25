"""Pipeline skeleton tests for MemoryRecallPipeline.

These are the RED starting point for task 1.5/1.6: they pin the seven-stage
ordering, diagnostic aggregation, recoverable degradation handling and fatal
error propagation, using fake stages that satisfy the stage protocols.
"""

import pytest

from agents_memory.models import MemoryRecord, MemoryScope, MemoryType
from agents_memory.pipeline.recall import MemoryRecallPipeline
from agents_memory.recall.errors import RecallStorageUnavailable
from agents_memory.recall.selection import SetSelector
from agents_memory.recall.models import (
    ContextAssembly,
    DegradationCode,
    EligibleCandidate,
    EvidenceGroup,
    EvidenceItem,
    EvidenceRole,
    ExecutionStatus,
    RecallIntent,
    RecallLane,
    RecallPlan,
    RecallRequest,
    RetrievedCandidate,
    ScoredCandidate,
    Sufficiency,
)

STAGE_ORDER = (
    "intent",
    "planning",
    "retrieval",
    "filtering",
    "scoring",
    "evidence",
    "selection",
    "assembly",
)


def _request(query: str = "what did I decide") -> RecallRequest:
    return RecallRequest(user_id="u1", agent_id="a1", session_id="s1", query=query)


def _record(memory_id: str = "m1") -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
        type=MemoryType.FACT,
        content="a decision",
        importance=5,
        confidence=0.8,
    )


def _evidence_group(memory_id: str = "m1") -> EvidenceGroup:
    return EvidenceGroup(
        group_id="g1",
        primary=EvidenceItem(
            evidence_id="e1",
            memory_id=memory_id,
            role=EvidenceRole.CURRENT,
            content="a decision",
            memory_type=MemoryType.FACT,
            scope=MemoryScope(user_id="u1"),
        ),
    )


class _Spy:
    """Fake stage implementing every stage protocol method.

    Each call records its stage name, optionally reports a degradation code,
    optionally raises a fatal error, and returns a pre-built result.
    """

    def __init__(
        self,
        name: str,
        log: list[str],
        *,
        result=None,
        degrade: DegradationCode | None = None,
        error: Exception | None = None,
    ) -> None:
        self._name = name
        self._log = log
        self._result = result
        self._degrade = degrade
        self._error = error

    def _handle(self, diag):
        self._log.append(self._name)
        if self._degrade is not None:
            diag.degrade(self._degrade)
        if self._error is not None:
            raise self._error
        return self._result

    def build(self, request, diag):
        return self._handle(diag)

    def plan(self, request, intent, diag):
        return self._handle(diag)

    def retrieve(self, request, plan, diag):
        return self._handle(diag)

    def filter(self, request, candidates, diag):
        return self._handle(diag)

    def score(self, request, candidates, diag):
        return self._handle(diag)

    def resolve(self, request, scored, diag):
        return self._handle(diag)

    def select(self, request, groups, diag):
        return self._handle(diag)

    def assemble(self, request, groups, diag):
        return self._handle(diag)


def _build_pipeline(
    log: list[str],
    *,
    retriever_degrade: DegradationCode | None = None,
    retriever_error: Exception | None = None,
) -> MemoryRecallPipeline:
    return MemoryRecallPipeline(
        intent_builder=_Spy("intent", log, result=RecallIntent(primary_query="q")),
        planner=_Spy(
            "planning",
            log,
            result=RecallPlan(
                intent=RecallIntent(primary_query="q"),
                lanes=(),
                global_candidate_limit=10,
            ),
        ),
        retriever=_Spy(
            "retrieval",
            log,
            result=(RetrievedCandidate(memory_id="m1"), RetrievedCandidate(memory_id="m2")),
            degrade=retriever_degrade,
            error=retriever_error,
        ),
        filter=_Spy(
            "filtering",
            log,
            result=(EligibleCandidate(memory_id="m1", record=_record("m1")),),
        ),
        scorer=_Spy(
            "scoring",
            log,
            result=(ScoredCandidate(memory_id="m1", record=_record("m1"), utility=0.8),),
        ),
        resolver=_Spy("evidence", log, result=(_evidence_group("m1"),)),
        selector=_Spy("selection", log, result=(_evidence_group("m1"),)),
        assembler=_Spy(
            "assembly",
            log,
            result=ContextAssembly(
                context="ctx",
                sufficiency=Sufficiency.SUFFICIENT,
                intent_summary="decide",
                lanes_used=(RecallLane.SESSION_CURRENT,),
            ),
        ),
    )


class TestPipelineStageOrder:
    def test_stages_execute_in_seven_stage_order(self):
        log: list[str] = []
        pipeline = _build_pipeline(log)
        pipeline.run(_request())
        assert log == list(STAGE_ORDER)

    def test_no_stage_skipped_when_empty_results(self):
        log: list[str] = []
        pipeline = MemoryRecallPipeline(
            intent_builder=_Spy("intent", log, result=RecallIntent(primary_query="q")),
            planner=_Spy(
                "planning",
                log,
                result=RecallPlan(
                    intent=RecallIntent(primary_query="q"),
                    lanes=(),
                    global_candidate_limit=0,
                ),
            ),
            retriever=_Spy("retrieval", log, result=()),
            filter=_Spy("filtering", log, result=()),
            scorer=_Spy("scoring", log, result=()),
            resolver=_Spy("evidence", log, result=()),
            selector=_Spy("selection", log, result=()),
            assembler=_Spy(
                "assembly",
                log,
                result=ContextAssembly(context="", sufficiency=Sufficiency.EMPTY),
            ),
        )
        result = pipeline.run(_request())
        assert log == list(STAGE_ORDER)
        assert result.metadata.sufficiency is Sufficiency.EMPTY


class TestPipelineDiagnosticsAggregation:
    def test_counts_aggregated_into_metadata(self):
        log: list[str] = []
        result = _build_pipeline(log).run(_request())
        assert result.metadata.candidate_count == 2
        assert result.metadata.filtered_count == 1
        assert result.metadata.final_count == 1

    def test_sufficiency_and_lanes_propagated_from_assembly(self):
        result = _build_pipeline([]).run(_request())
        assert result.metadata.sufficiency is Sufficiency.SUFFICIENT
        assert result.metadata.lanes_used == (RecallLane.SESSION_CURRENT,)
        assert result.metadata.intent_summary == "decide"
        assert result.context == "ctx"

    def test_complete_status_when_no_degradation(self):
        result = _build_pipeline([]).run(_request())
        assert result.metadata.execution_status is ExecutionStatus.COMPLETE
        assert result.metadata.degradations == ()


class TestPipelineRecoverableDegradation:
    def test_recoverable_degradation_marks_degraded_and_returns_result(self):
        log: list[str] = []
        pipeline = _build_pipeline(log, retriever_degrade=DegradationCode.VECTOR_INDEX_UNAVAILABLE)
        result = pipeline.run(_request())
        assert result.metadata.execution_status is ExecutionStatus.DEGRADED
        assert DegradationCode.VECTOR_INDEX_UNAVAILABLE in result.metadata.degradations
        # Pipeline still completed every stage despite the degradation.
        assert log == list(STAGE_ORDER)

    def test_degradation_does_not_duplicate_code(self):
        pipeline = _build_pipeline([], retriever_degrade=DegradationCode.VECTOR_INDEX_UNAVAILABLE)
        result = pipeline.run(_request())
        assert result.metadata.degradations.count(DegradationCode.VECTOR_INDEX_UNAVAILABLE) == 1


class TestPipelineFatalErrors:
    def test_storage_unavailable_propagates_as_fatal(self):
        pipeline = _build_pipeline([], retriever_error=RecallStorageUnavailable("sqlite down"))
        with pytest.raises(RecallStorageUnavailable):
            pipeline.run(_request())

    def test_fatal_error_does_not_fabricate_result(self):
        pipeline = _build_pipeline([], retriever_error=RecallStorageUnavailable("sqlite down"))
        with pytest.raises(RecallStorageUnavailable):
            pipeline.run(_request())
        # No partial RecallResult is ever returned for a fatal failure.


class _IdRecord:
    def __init__(self, mid: str) -> None:
        self.id = mid


class _FakeRepo:
    def __init__(self, present_ids: set[str]) -> None:
        self._present = set(present_ids)

    def revalidate_final_state(self, memory_ids, *, user_id):  # noqa: ARG002
        return [_IdRecord(mid) for mid in memory_ids if mid in self._present]


def _evidence_group(gid: str) -> EvidenceGroup:
    return EvidenceGroup(
        group_id=gid,
        primary=EvidenceItem(
            evidence_id=f"{gid}:c",
            memory_id=gid,
            role=EvidenceRole.CURRENT,
            content=gid,
            memory_type=MemoryType.FACT,
            scope=MemoryScope(user_id="u1"),
            importance=5,
        ),
    )


class TestPipelineFinalRevalidation:
    def _pipeline(self, groups, repo):
        return MemoryRecallPipeline(
            intent_builder=_Spy("intent", [], result=RecallIntent(primary_query="q")),
            planner=_Spy(
                "planning",
                [],
                result=RecallPlan(
                    intent=RecallIntent(primary_query="q"),
                    lanes=(),
                    global_candidate_limit=10,
                ),
            ),
            retriever=_Spy("retrieval", [], result=()),
            filter=_Spy("filtering", [], result=()),
            scorer=_Spy("scoring", [], result=()),
            resolver=_Spy("evidence", [], result=groups),
            selector=SetSelector(),
            assembler=_Spy(
                "assembly",
                [],
                result=ContextAssembly(context="x", sufficiency=Sufficiency.SUFFICIENT),
            ),
            repository=repo,
        )

    def test_drift_triggers_reselection(self):
        from agents_memory.recall.diagnostics import RecallDiagnostics
        from agents_memory.recall.selection import SetSelector  # noqa: F401

        groups = (_evidence_group("a"), _evidence_group("b"))
        pipeline = self._pipeline(groups, _FakeRepo(present_ids={"a"}))
        diag = RecallDiagnostics()
        selected = pipeline._final_revalidation(_request(), groups, groups, diag)
        assert all(g.group_id != "b" for g in selected)
        assert any("drift" in note for note in diag.notes)

    def test_no_drift_keeps_selection(self):
        from agents_memory.recall.diagnostics import RecallDiagnostics

        groups = (_evidence_group("a"), _evidence_group("b"))
        pipeline = self._pipeline(groups, _FakeRepo(present_ids={"a", "b"}))
        diag = RecallDiagnostics()
        selected = pipeline._final_revalidation(_request(), groups, groups, diag)
        assert {g.group_id for g in selected} == {"a", "b"}
        assert diag.notes == ()

    def test_no_repository_is_noop(self):
        from agents_memory.recall.diagnostics import RecallDiagnostics

        pipeline = _build_pipeline([])
        groups = (_evidence_group("a"),)
        assert (
            pipeline._final_revalidation(_request(), groups, groups, RecallDiagnostics()) == groups
        )

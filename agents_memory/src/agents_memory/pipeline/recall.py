"""Memory Recall pipeline orchestration skeleton.

The pipeline only wires stages, passes structured stage objects, collects
diagnostics, propagates fatal domain errors and assembles the final
``RecallResult``. Stage behavior is injected via protocols so tests can
substitute fake stages; real stages are delivered in later milestones.

Degradation model:
- Recoverable failures are reported via ``RecallDiagnostics.degrade(...)``;
  the stage returns a degraded value and the result is still returned with
  ``execution_status = DEGRADED``.
- Fatal ``RecallError`` subclasses are never caught here: they propagate so
  Recall never fabricates a normal or empty result when it could not actually
  verify candidates.
"""

from typing import Any, Protocol

from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    ContextAssembly,
    DegradationCode,
    EligibleCandidate,
    EvidenceGroup,
    ExecutionStatus,
    RecallIntent,
    RecallMetadata,
    RecallPlan,
    RecallRequest,
    RecallResult,
    RetrievedCandidate,
    ScoredCandidate,
)


class IntentBuilder(Protocol):
    def build(self, request: RecallRequest, diag: RecallDiagnostics) -> RecallIntent: ...


class RecallPlanner(Protocol):
    def plan(
        self,
        request: RecallRequest,
        intent: RecallIntent,
        diag: RecallDiagnostics,
    ) -> RecallPlan: ...


class CandidateRetriever(Protocol):
    def retrieve(
        self,
        request: RecallRequest,
        plan: RecallPlan,
        diag: RecallDiagnostics,
    ) -> tuple[RetrievedCandidate, ...]: ...


class EligibilityFilter(Protocol):
    def filter(
        self,
        request: RecallRequest,
        candidates: tuple[RetrievedCandidate, ...],
        diag: RecallDiagnostics,
    ) -> tuple[EligibleCandidate, ...]: ...


class UtilityScorer(Protocol):
    def score(
        self,
        request: RecallRequest,
        candidates: tuple[EligibleCandidate, ...],
        diag: RecallDiagnostics,
    ) -> tuple[ScoredCandidate, ...]: ...


class EvidenceResolver(Protocol):
    def resolve(
        self,
        request: RecallRequest,
        scored: tuple[ScoredCandidate, ...],
        diag: RecallDiagnostics,
    ) -> tuple[EvidenceGroup, ...]: ...


class SetSelector(Protocol):
    def select(
        self,
        request: RecallRequest,
        groups: tuple[EvidenceGroup, ...],
        diag: RecallDiagnostics,
    ) -> tuple[EvidenceGroup, ...]: ...


class ContextAssembler(Protocol):
    def assemble(
        self,
        request: RecallRequest,
        groups: tuple[EvidenceGroup, ...],
        diag: RecallDiagnostics,
    ) -> ContextAssembly: ...


class MemoryRecallPipeline:
    """Dependency-injected seven-stage Recall pipeline skeleton."""

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

    def __init__(
        self,
        intent_builder: IntentBuilder,
        planner: RecallPlanner,
        retriever: CandidateRetriever,
        filter: EligibilityFilter,
        scorer: UtilityScorer,
        resolver: EvidenceResolver,
        selector: SetSelector,
        assembler: ContextAssembler,
        repository: Any = None,
    ) -> None:
        self.intent_builder = intent_builder
        self.planner = planner
        self.retriever = retriever
        self.filter = filter
        self.scorer = scorer
        self.resolver = resolver
        self.selector = selector
        self.assembler = assembler
        self.repository = repository

    def run(self, request: RecallRequest) -> RecallResult:
        diag = RecallDiagnostics()
        intent = self.intent_builder.build(request, diag)
        plan = self.planner.plan(request, intent, diag)
        candidates = self.retriever.retrieve(request, plan, diag)
        eligible = self.filter.filter(request, candidates, diag)
        scored = self.scorer.score(request, eligible, diag)
        groups = self.resolver.resolve(request, scored, diag)
        selected = self.selector.select(request, groups, diag)
        selected = self._final_revalidation(request, selected, groups, diag)
        assembly = self.assembler.assemble(request, selected, diag)
        metadata = RecallMetadata(
            intent_summary=assembly.intent_summary,
            lanes_used=assembly.lanes_used,
            candidate_count=len(candidates),
            filtered_count=len(eligible),
            final_count=len(selected),
            sufficiency=assembly.sufficiency,
            execution_status=(
                ExecutionStatus.DEGRADED if diag.degradations else ExecutionStatus.COMPLETE
            ),
            degradations=diag.degradations,
            diagnostics=diag.notes if request.diagnostic else (),
        )
        return RecallResult(
            context=assembly.context,
            evidence=selected,
            metadata=metadata,
        )

    def _final_revalidation(
        self,
        request: RecallRequest,
        selected: tuple[EvidenceGroup, ...],
        groups: tuple[EvidenceGroup, ...],
        diag: RecallDiagnostics,
    ) -> tuple[EvidenceGroup, ...]:
        """Lightweight final-state revalidation before assembly (design 12.7).

        Reselects once from still-present groups if any selected memory drifted
        (deleted between scoring and output). Does not hold a long transaction.
        """

        if self.repository is None or not selected:
            return selected
        memory_ids = tuple(mid for group in selected for mid in _group_memory_ids(group))
        if not memory_ids:
            return selected
        try:
            records = self.repository.revalidate_final_state(memory_ids, user_id=request.user_id)
        except Exception as exc:  # noqa: BLE001 (recoverable: keep selection)
            diag.degrade(DegradationCode.INCOMPLETE_RELATION_CHAIN, type(exc).__name__)
            return selected
        present = {record.id for record in records}
        if all(mid in present for group in selected for mid in _group_memory_ids(group)):
            return selected
        diag.note("final state drift: reselected once")
        stable = tuple(
            group for group in groups if all(mid in present for mid in _group_memory_ids(group))
        )
        return self.selector.select(request, stable, diag)


def _group_memory_ids(group: EvidenceGroup) -> tuple[str, ...]:
    return tuple(
        item.memory_id
        for item in (
            group.primary,
            *group.supporting,
            *group.historical,
            *group.conflicting,
        )
    )

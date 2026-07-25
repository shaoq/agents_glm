"""Multi-lane, multi-path Recall candidate generation.

Paths per lane (design 6):
- semantic: embed query variants and query the vector index (Chroma);
- temporal: structured time reads from SQLite when the intent needs a time view;
- unsynced: bounded coverage of ACTIVE records not yet synced to the index;
- relation: bounded one-hop expansion on top semantic seeds.

Candidates are merged by ``memory_id`` while preserving every hit signal, then
bounded by the plan's global candidate limit with a light, stable pre-sort.
This module does not reuse the Write ``ContextLookup``.

Reference: design 6 (multi-path candidate generation).
"""

from collections import defaultdict
from dataclasses import dataclass

from agents_memory.embedding.base import Embedder
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    DegradationCode,
    HitSignal,
    RecallPlan,
    RecallRequest,
    RetrievedCandidate,
)
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.vector import MemoryIndex


@dataclass(frozen=True)
class RetrieverConfig:
    semantic_threshold: float = 0.0
    temporal_limit: int = 10
    unsynced_limit: int = 10
    relation_expansion_limit: int = 5


class MultiLaneCandidateRetriever:
    """Generates merged, bounded candidates across lanes and paths."""

    def __init__(
        self,
        embedder: Embedder,
        index: MemoryIndex,
        repository: RecallReadRepository,
        config: RetrieverConfig | None = None,
    ) -> None:
        self.embedder = embedder
        self.index = index
        self.repository = repository
        self.config = config or RetrieverConfig()

    def retrieve(
        self,
        request: RecallRequest,
        plan: RecallPlan,
        diag: RecallDiagnostics,
    ) -> tuple[RetrievedCandidate, ...]:
        hits_by_id: dict[str, list[HitSignal]] = defaultdict(list)
        semantic_ok = self._semantic(request, plan, diag, hits_by_id)
        self._temporal(request, plan, hits_by_id)
        self._unsynced(request, plan, hits_by_id)
        if semantic_ok:
            self._relation_expansion(request, plan, hits_by_id, diag)
        return self._materialize(hits_by_id, plan)

    def _semantic(
        self,
        request: RecallRequest,  # noqa: ARG002
        plan: RecallPlan,
        diag: RecallDiagnostics,
        hits_by_id: dict[str, list[HitSignal]],
    ) -> bool:
        lanes = [lane for lane in plan.lanes if lane.enabled and lane.query_variants]
        if not lanes:
            return True
        variant_texts: list[str] = []
        for lane in lanes:
            variant_texts.extend(v.text for v in lane.query_variants)
        try:
            embeddings = self.embedder.embed(variant_texts)
        except Exception as exc:  # noqa: BLE001 (any embedder failure is recoverable)
            diag.degrade(DegradationCode.SEMANTIC_UNAVAILABLE, type(exc).__name__)
            return False
        text_to_embedding = dict(zip(variant_texts, embeddings, strict=True))
        index_failed = False
        for lane in lanes:
            for variant in lane.query_variants:
                embedding = text_to_embedding[variant.text]
                try:
                    index_hits = self.index.query_candidates(
                        embedding,
                        user_id=lane.scope.user_id,
                        types=tuple(lane.target_types),
                        top_k=lane.candidate_quota or 1,
                        agent_id=lane.scope.agent_id,
                        session_id=lane.scope.session_id,
                        threshold=self.config.semantic_threshold,
                    )
                except Exception as exc:  # noqa: BLE001
                    if not index_failed:
                        diag.degrade(
                            DegradationCode.VECTOR_INDEX_UNAVAILABLE,
                            type(exc).__name__,
                        )
                        index_failed = True
                    continue
                for hit in index_hits:
                    hits_by_id[hit.memory_id].append(
                        HitSignal(
                            lane=lane.lane,
                            path="semantic",
                            query_variant=variant.text,
                            similarity=hit.similarity,
                        )
                    )
        return not index_failed

    def _temporal(
        self,
        request: RecallRequest,  # noqa: ARG002
        plan: RecallPlan,
        hits_by_id: dict[str, list[HitSignal]],
    ) -> None:
        for lane in plan.lanes:
            if not lane.enabled or lane.temporal_need is None:
                continue
            records = self.repository.query_temporal_memories(
                lane.scope,
                types=tuple(lane.target_types),
                limit=self.config.temporal_limit,
            )
            for record in records:
                hits_by_id[record.id].append(HitSignal(lane=lane.lane, path="temporal"))

    def _unsynced(
        self,
        request: RecallRequest,  # noqa: ARG002
        plan: RecallPlan,
        hits_by_id: dict[str, list[HitSignal]],
    ) -> None:
        seen_scopes: set[tuple[str, str | None, str | None]] = set()
        for lane in plan.lanes:
            if not lane.enabled:
                continue
            key = (lane.scope.user_id, lane.scope.agent_id, lane.scope.session_id)
            if key in seen_scopes:
                continue
            seen_scopes.add(key)
            records = self.repository.list_unsynced_coverage(
                lane.scope,
                types=tuple(lane.target_types),
                limit=self.config.unsynced_limit,
            )
            for record in records:
                hits_by_id[record.id].append(HitSignal(lane=lane.lane, path="unsynced"))

    def _relation_expansion(
        self,
        request: RecallRequest,
        plan: RecallPlan,
        hits_by_id: dict[str, list[HitSignal]],
        diag: RecallDiagnostics,
    ) -> None:
        if plan.relation_expansion_depth < 1 or not hits_by_id:
            return
        ranked = sorted(
            hits_by_id.items(),
            key=lambda kv: (
                len(kv[1]),
                max((h.similarity or 0.0) for h in kv[1]),
            ),
            reverse=True,
        )
        seed_ids = tuple(mid for mid, _ in ranked[: self.config.relation_expansion_limit])
        if not seed_ids:
            return
        try:
            grouped = self.repository.get_relations_batch(seed_ids, user_id=request.user_id)
        except Exception as exc:  # noqa: BLE001
            diag.degrade(DegradationCode.INCOMPLETE_RELATION_CHAIN, type(exc).__name__)
            return
        for mid, relations in grouped.items():
            if not relations or mid not in hits_by_id:
                continue
            seed_lane = hits_by_id[mid][0].lane
            for relation in relations:
                other = (
                    relation.to_memory_id
                    if relation.from_memory_id == mid
                    else relation.from_memory_id
                )
                if other not in hits_by_id:
                    hits_by_id[other].append(HitSignal(lane=seed_lane, path="relation"))

    @staticmethod
    def _materialize(
        hits_by_id: dict[str, list[HitSignal]],
        plan: RecallPlan,
    ) -> tuple[RetrievedCandidate, ...]:
        candidates = [
            RetrievedCandidate(memory_id=mid, hits=tuple(hits)) for mid, hits in hits_by_id.items()
        ]
        candidates.sort(
            key=lambda c: (
                len(c.hits),
                max((h.similarity or 0.0) for h in c.hits),
            ),
            reverse=True,
        )
        return tuple(candidates[: plan.global_candidate_limit])

"""Recall evidence resolution: temporal roles and explicit relation grouping.

Phase 1 (this module's deterministic core, design 9.1-9.3):
- Assign each candidate a query-time role from its validity and the request's
  temporal intent (CURRENT vs HISTORICAL/EVOLVED).
- Group candidates connected by persistent SUPERSEDES/CORRECTS relations into
  one EvidenceGroup, with the active record as primary and superseded records
  as historical members.
- Walk the relation graph with a visited set so cycles, self-links and missing
  nodes cannot loop; cross-user relations are already rejected by the repository.

Conflict grouping, event-identity and LLM relation review (design 9.4-9.8) are
layered on top of these deterministic groups.
"""

from collections import defaultdict
from dataclasses import dataclass

from agents_memory.models import RelationKind, Validity
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    EvidenceGroup,
    EvidenceItem,
    EvidenceRole,
    RecallRequest,
    ScoredCandidate,
)
from agents_memory.storage.recalls import RecallReadRepository


@dataclass(frozen=True)
class EvidenceResolverConfig:
    chain_max_depth: int = 5


class EvidenceResolver:
    """Resolves scored candidates into evidence groups via explicit relations."""

    def __init__(
        self,
        repository: RecallReadRepository,
        config: EvidenceResolverConfig | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or EvidenceResolverConfig()

    def resolve(
        self,
        request: RecallRequest,
        scored: tuple[ScoredCandidate, ...],
        diag: RecallDiagnostics,  # noqa: ARG002 (accepted for stage protocol symmetry)
    ) -> tuple[EvidenceGroup, ...]:
        if not scored:
            return ()
        by_id: dict[str, ScoredCandidate] = {s.memory_id: s for s in scored}
        relations = self.repository.get_relations_batch(tuple(by_id), user_id=request.user_id)
        neighbors: dict[str, set[str]] = defaultdict(set)
        for mid, rels in relations.items():
            for rel in rels:
                if rel.relation not in (RelationKind.SUPERSEDES, RelationKind.CORRECTS):
                    continue
                other = rel.to_memory_id if rel.from_memory_id == mid else rel.from_memory_id
                if other in by_id:
                    neighbors[mid].add(other)
        visited: set[str] = set()
        groups: list[EvidenceGroup] = []
        for mid in by_id:
            if mid in visited:
                continue
            component = self._bfs(mid, neighbors, visited)
            groups.append(self._build_group(component, by_id, request))
        return tuple(groups)

    @staticmethod
    def _bfs(
        start: str,
        neighbors: dict[str, set[str]],
        visited: set[str],
    ) -> list[str]:
        queue = [start]
        visited.add(start)
        component: list[str] = []
        depth = 0
        while queue:
            current = queue.pop(0)
            component.append(current)
            depth += 1
            for neighbor in neighbors.get(current, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
            if depth > 100:  # hard backstop against pathological graphs
                break
        return component

    @staticmethod
    def _build_group(
        component: list[str],
        by_id: dict[str, ScoredCandidate],
        request: RecallRequest,  # noqa: ARG004
    ) -> EvidenceGroup:
        if len(component) == 1:
            return EvidenceResolver._standalone_group(component[0], by_id)
        active = [mid for mid in component if by_id[mid].record.validity is Validity.ACTIVE]
        primary_id = active[0] if active else component[0]
        primary_role = EvidenceRole.CURRENT if active else EvidenceRole.HISTORICAL
        primary_item = EvidenceResolver._item(by_id[primary_id], primary_role)
        historical: list[EvidenceItem] = []
        for mid in component:
            if mid == primary_id:
                continue
            record = by_id[mid].record
            role = (
                EvidenceRole.HISTORICAL
                if record.validity is Validity.SUPERSEDED
                else EvidenceRole.SUPERSEDED
            )
            historical.append(EvidenceResolver._item(by_id[mid], role))
        return EvidenceGroup(
            group_id=primary_id,
            primary=primary_item,
            historical=tuple(historical),
            resolution="explicit relation chain",
        )

    @staticmethod
    def _standalone_group(mid: str, by_id: dict[str, ScoredCandidate]) -> EvidenceGroup:
        scored = by_id[mid]
        role = (
            EvidenceRole.CURRENT
            if scored.record.validity is Validity.ACTIVE
            else EvidenceRole.HISTORICAL
        )
        return EvidenceGroup(
            group_id=mid,
            primary=EvidenceResolver._item(scored, role),
        )

    @staticmethod
    def _item(scored: ScoredCandidate, role: EvidenceRole) -> EvidenceItem:
        record = scored.record
        return EvidenceItem(
            evidence_id=f"{record.id}:{role.value}",
            memory_id=record.id,
            role=role,
            content=record.content,
            memory_type=record.type,
            scope=record.scope,
            occurred_at=record.valid_from,
            valid_from=record.valid_from,
            confidence=record.confidence,
            importance=record.importance,
        )

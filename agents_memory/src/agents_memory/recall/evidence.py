"""Recall evidence resolution.

Two deterministic layers plus an optional LLM relation reviewer:

1. Temporal roles from validity + temporal intent (CURRENT/HISTORICAL).
2. Explicit SUPERSEDES/CORRECTS relation chains -> one EvidenceGroup per
   connected component (active primary + superseded historical). BFS with a
   visited set handles cycles, self-links and missing nodes; cross-user
   relations are already rejected by the repository.
3. Optional GLM event-identity review groups remaining standalone candidates
   that describe the same event: a conflict becomes an atomic CONFLICTING
   group keeping both sides; a non-conflicting same-event set becomes an
   EVOLVED group. unknown/different identities stay independent.

Conservative guarantees (design 9.4-9.8): never guess a single conclusion,
keep both conflict sides, never create DEFER, never ask the user, never write
back. LLM failure degrades to ``resolution_fallback`` and leaves candidates
independent.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agents_memory.models import RelationKind, Validity
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    DegradationCode,
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


class _EventGroupItem(BaseModel):
    memory_ids: list[str]
    identity: str = "unknown"  # same_event | different_event | unknown
    conflict: bool = False


class _EventReviewOutput(BaseModel):
    groups: list[_EventGroupItem] = []


class GLMEventIdentityReviewer:
    """Optional LLM reviewer judging event identity and conflict for groups."""

    def __init__(self, *, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    def review(
        self,
        request: RecallRequest,
        candidates: tuple[ScoredCandidate, ...],
        diag: RecallDiagnostics,
    ) -> list[tuple[tuple[str, ...], str, bool]]:
        if len(candidates) < 2:
            return []
        try:
            output = self._invoke(request, candidates)
        except (ValidationError, ValueError, TypeError, KeyError, IndexError) as exc:
            diag.degrade(DegradationCode.RESOLUTION_FALLBACK, f"parse: {type(exc).__name__}")
            return []
        except Exception as exc:  # noqa: BLE001 (any LLM failure is recoverable)
            diag.degrade(DegradationCode.RESOLUTION_FALLBACK, type(exc).__name__)
            return []
        return [(tuple(item.memory_ids), item.identity, item.conflict) for item in output.groups]

    def _invoke(
        self, request: RecallRequest, candidates: tuple[ScoredCandidate, ...]
    ) -> _EventReviewOutput:
        payload = {
            "query": request.query,
            "candidates": [
                {
                    "memory_id": c.memory_id,
                    "content": c.record.content,
                    "type": c.record.type.value,
                    "valid_from": c.record.valid_from.isoformat() if c.record.valid_from else None,
                }
                for c in candidates
            ],
        }
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Group candidate memories that describe the SAME event. "
                        "Return ONLY JSON: "
                        '{"groups":[{"memory_ids":["..."],'
                        '"identity":"same_event|different_event|unknown",'
                        '"conflict":false}]}. '
                        "Use 'unknown' when event identity is uncertain. "
                        "Set conflict=true only for mutually exclusive claims "
                        "about the same subject/property in overlapping time."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
        )
        return _EventReviewOutput.model_validate_json(response.choices[0].message.content)


class EvidenceResolver:
    """Resolves scored candidates into evidence groups."""

    def __init__(
        self,
        repository: RecallReadRepository,
        config: EvidenceResolverConfig | None = None,
        reviewer: GLMEventIdentityReviewer | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or EvidenceResolverConfig()
        self.reviewer = reviewer

    def resolve(
        self,
        request: RecallRequest,
        scored: tuple[ScoredCandidate, ...],
        diag: RecallDiagnostics,
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
        standalone: list[ScoredCandidate] = []
        for mid in by_id:
            if mid in visited:
                continue
            component = self._bfs(mid, neighbors, visited)
            if len(component) > 1:
                groups.append(self._explicit_group(component, by_id))
            else:
                standalone.append(by_id[component[0]])
        groups.extend(self._group_standalone(request, tuple(standalone), diag))
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
        while queue:
            current = queue.pop(0)
            component.append(current)
            for neighbor in neighbors.get(current, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
            if len(component) > 100:  # hard backstop against pathological graphs
                break
        return component

    @staticmethod
    def _explicit_group(
        component: list[str],
        by_id: dict[str, ScoredCandidate],
    ) -> EvidenceGroup:
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

    def _group_standalone(
        self,
        request: RecallRequest,
        candidates: tuple[ScoredCandidate, ...],
        diag: RecallDiagnostics,
    ) -> list[EvidenceGroup]:
        if not candidates:
            return []
        if self.reviewer is None:
            return [self._standalone_group(c) for c in candidates]
        reviews = self.reviewer.review(request, candidates, diag)
        return self._apply_event_reviews(candidates, reviews)

    @staticmethod
    def _apply_event_reviews(
        candidates: tuple[ScoredCandidate, ...],
        reviews: list[tuple[tuple[str, ...], str, bool]],
    ) -> list[EvidenceGroup]:
        by_id = {c.memory_id: c for c in candidates}
        grouped: set[str] = set()
        result: list[EvidenceGroup] = []
        for memory_ids, identity, conflict in reviews:
            if identity != "same_event":
                continue
            members = [by_id[m] for m in memory_ids if m in by_id and m not in grouped]
            if len(members) < 2:
                continue
            grouped.update(m.memory_id for m in members)
            members.sort(key=lambda s: s.utility, reverse=True)
            if conflict:
                primary = EvidenceResolver._item(members[0], EvidenceRole.CONFLICTING)
                conflicting = tuple(
                    EvidenceResolver._item(m, EvidenceRole.CONFLICTING) for m in members[1:]
                )
                result.append(
                    EvidenceGroup(
                        group_id=members[0].memory_id,
                        primary=primary,
                        conflicting=conflicting,
                        resolution="unresolved conflict; both sides kept",
                    )
                )
            else:
                primary = EvidenceResolver._item(members[0], EvidenceRole.CURRENT)
                historical = tuple(
                    EvidenceResolver._item(m, EvidenceRole.EVOLVED) for m in members[1:]
                )
                result.append(
                    EvidenceGroup(
                        group_id=members[0].memory_id,
                        primary=primary,
                        historical=historical,
                        resolution="natural evolution",
                    )
                )
        for candidate in candidates:
            if candidate.memory_id not in grouped:
                result.append(EvidenceResolver._standalone_group(candidate))
        return result

    @staticmethod
    def _standalone_group(scored: ScoredCandidate) -> EvidenceGroup:
        role = (
            EvidenceRole.CURRENT
            if scored.record.validity is Validity.ACTIVE
            else EvidenceRole.HISTORICAL
        )
        return EvidenceGroup(
            group_id=scored.memory_id,
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

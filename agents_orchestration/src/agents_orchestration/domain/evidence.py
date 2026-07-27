"""Evidence and EvidenceSet (design Decision 9 / tasks 8.4-8.7).

All external content (Memory, RAG, Web, Model) is normalized into untrusted
``Evidence`` carrying source identity, freshness, citation, trust and
degradation metadata. ``EvidenceSet`` is the immutable Join output with source
deduplication, conflict preservation and sufficiency.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.enums import Sufficiency


class SourceKind(StrEnum):
    """Provenance of a piece of evidence."""

    MEMORY = "memory"
    RAG = "rag"
    WEB = "web"
    MODEL = "model"
    SYNTHETIC = "synthetic"


class SourceIdentity(BaseModel):
    """Stable identity used for deduplication (task 8.5)."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    source_kind: SourceKind
    uri: str | None = None
    retrieved_at: datetime | None = None
    trust: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def dedup_key(self) -> tuple[str, str]:
        """Identity used to avoid overstating independent evidence count."""

        if self.uri:
            return (self.source_kind.value, self.uri)
        return (self.source_kind.value, self.source_id)


class Degradation(BaseModel):
    """Disclosed degradation rather than faked success (design Decision 13)."""

    model_config = ConfigDict(frozen=True)

    flag: str
    reason: str
    fallback_used: bool = False


class Evidence(BaseModel):
    """A single piece of normalized, untrusted evidence (task 8.4)."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source: SourceIdentity
    content_ref: str | None = None
    content_text: str | None = None
    citation: str | None = None
    freshness_at: datetime | None = None
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    degradation: tuple[Degradation, ...] = Field(default_factory=tuple)
    conflict_group: str | None = None
    is_untrusted: bool = True

    @property
    def dedup_key(self) -> tuple[str, str]:
        return self.source.dedup_key


class ConflictGroup(BaseModel):
    """A material conflict between evidences (task 8.6)."""

    model_config = ConfigDict(frozen=True)

    group_id: str
    evidence_ids: tuple[str, ...]
    description: str


class EvidenceSet(BaseModel):
    """Immutable Join output (task 8.4-8.8)."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    task_id: str | None = None
    evidences: tuple[Evidence, ...] = Field(default_factory=tuple)
    conflicts: tuple[ConflictGroup, ...] = Field(default_factory=tuple)
    sufficiency: Sufficiency = Sufficiency.UNKNOWN
    missing_required: bool = False
    independent_count: int = Field(default=0, ge=0)
    degradations: tuple[Degradation, ...] = Field(default_factory=tuple)

    @classmethod
    def join(
        cls,
        *,
        run_id: str,
        task_id: str | None,
        evidences: tuple[Evidence, ...],
        required: bool,
    ) -> EvidenceSet:
        """Deterministic Join: dedupe by source identity, flag conflicts.

        Conflict detection here is structural (same dedup key with divergent
        content hash/text); material semantic conflict detection is performed by
        the Analyst/reviewer workers and merged later.
        """

        deduped: list[Evidence] = []
        seen: set[tuple[str, str]] = set()
        for ev in evidences:
            if ev.dedup_key in seen:
                continue
            seen.add(ev.dedup_key)
            deduped.append(ev)

        conflicts = _structural_conflicts(deduped)
        independent = len(deduped)
        suff = _sufficiency(independent, conflicts, required)

        return cls(
            run_id=run_id,
            task_id=task_id,
            evidences=tuple(deduped),
            conflicts=tuple(conflicts),
            sufficiency=suff,
            missing_required=required and independent == 0,
            independent_count=independent,
        )


def _structural_conflicts(evidences: list[Evidence]) -> list[ConflictGroup]:
    """Group evidences that share a citation but diverge in content."""

    groups: list[ConflictGroup] = []
    by_citation: dict[str, list[Evidence]] = {}
    for ev in evidences:
        if ev.citation:
            by_citation.setdefault(ev.citation, []).append(ev)
    for citation, evs in by_citation.items():
        if len(evs) < 2:
            continue
        contents = {ev.content_ref or ev.content_text or ev.evidence_id for ev in evs}
        if len(contents) > 1:
            groups.append(
                ConflictGroup(
                    group_id=f"conflict:{citation}",
                    evidence_ids=tuple(ev.evidence_id for ev in evs),
                    description=f"Divergent content for citation {citation}",
                )
            )
    return groups


def _sufficiency(
    independent: int,
    conflicts: list[ConflictGroup],
    required: bool,
) -> Sufficiency:
    if conflicts:
        return Sufficiency.CONFLICTED
    if required and independent == 0:
        return Sufficiency.INSUFFICIENT
    if independent == 0:
        return Sufficiency.UNKNOWN
    return Sufficiency.SUFFICIENT


class Usage(BaseModel):
    """Resource usage reported by capabilities and aggregated into the ledger."""

    model_config = ConfigDict(frozen=True)

    tokens: int = 0
    cost_usd: Decimal = Field(default=Decimal("0"))
    latency_ms: int = 0
    retries: int = 0

    def add(self, other: Usage) -> Usage:
        return Usage(
            tokens=self.tokens + other.tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_ms=self.latency_ms + other.latency_ms,
            retries=self.retries + other.retries,
        )

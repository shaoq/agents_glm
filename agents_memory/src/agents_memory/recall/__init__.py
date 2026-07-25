"""Recall domain package.

Public API: ``RecallRequest``, ``RecallResult`` and ``RecallMetadata``.
Stage contracts and enums are exposed for service/CLI wiring and tests.
"""

from agents_memory.recall.models import (
    DegradationCode,
    EligibleCandidate,
    EvidenceGroup,
    EvidenceItem,
    EvidenceRole,
    ExecutionStatus,
    ExplicitConstraints,
    HitSignal,
    LanePlan,
    QueryVariant,
    RecallErrorCode,
    RecallIntent,
    RecallLane,
    RecallMetadata,
    RecallPlan,
    RecallRequest,
    RecallResult,
    RejectedCandidate,
    RejectionReason,
    RetrievedCandidate,
    ScoreComponent,
    ScoredCandidate,
    Sufficiency,
    TemporalIntent,
)

__all__ = [
    "DegradationCode",
    "EligibleCandidate",
    "EvidenceGroup",
    "EvidenceItem",
    "EvidenceRole",
    "ExecutionStatus",
    "ExplicitConstraints",
    "HitSignal",
    "LanePlan",
    "QueryVariant",
    "RecallErrorCode",
    "RecallIntent",
    "RecallLane",
    "RecallMetadata",
    "RecallPlan",
    "RecallRequest",
    "RecallResult",
    "RejectedCandidate",
    "RejectionReason",
    "RetrievedCandidate",
    "ScoreComponent",
    "ScoredCandidate",
    "Sufficiency",
    "TemporalIntent",
]

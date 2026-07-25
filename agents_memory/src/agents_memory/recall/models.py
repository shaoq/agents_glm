"""Recall domain models, enums and stage contracts.

These models are internal to the Recall pipeline except for the public
``RecallRequest`` / ``RecallResult`` / ``RecallMetadata`` triple. Stage
contracts are immutable (``FrozenModel``) and never write temporary Recall
fields back onto ``MemoryRecord``.

Reference: agents_memory/docs/specs/2026-07-25-memory-recall-pipeline-implementation.md
"""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from agents_memory.models import (
    FrozenModel,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemoryType,
    Message,
    TemporalAnchor,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RecallLane(StrEnum):
    """Three candidate lanes within a single user boundary."""

    SESSION_CURRENT = "session_current"
    AGENT_HISTORY = "agent_history"
    USER_SHARED = "user_shared"


class TemporalIntent(StrEnum):
    """What kind of temporal view the caller needs."""

    CURRENT_STATE = "current_state"
    POINT_IN_TIME = "point_in_time"
    INTERVAL = "interval"
    EVOLUTION = "evolution"


class EvidenceRole(StrEnum):
    """Query-time role assigned during evidence resolution.

    These roles do not replace storage-layer ``Validity`` or ``RelationKind``;
    they express how a record participates in the current query context.
    """

    CURRENT = "current"
    HISTORICAL = "historical"
    EVOLVED = "evolved"
    CORRECTED = "corrected"
    SUPERSEDED = "superseded"
    SUPPORTING = "supporting"
    CONFLICTING = "conflicting"
    INDEPENDENT = "independent"
    UNKNOWN_EVENT_IDENTITY = "unknown_event_identity"


class Sufficiency(StrEnum):
    """Business sufficiency of the assembled evidence."""

    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    CONFLICTED = "conflicted"
    EMPTY = "empty"


class ExecutionStatus(StrEnum):
    """Whether the pipeline executed all stages cleanly."""

    COMPLETE = "complete"
    DEGRADED = "degraded"


class DegradationCode(StrEnum):
    """Stable codes for recoverable degradation paths."""

    INTENT_FALLBACK = "intent_fallback"
    QUERY_REWRITE_FALLBACK = "query_rewrite_fallback"
    SEMANTIC_UNAVAILABLE = "semantic_unavailable"
    VECTOR_INDEX_UNAVAILABLE = "vector_index_unavailable"
    PARTIAL_LANE_FAILURE = "partial_lane_failure"
    SCORING_FALLBACK = "scoring_fallback"
    RESOLUTION_FALLBACK = "resolution_fallback"
    TOKEN_ESTIMATION_FALLBACK = "token_estimation_fallback"
    INCOMPLETE_RELATION_CHAIN = "incomplete_relation_chain"


class RejectionReason(StrEnum):
    """Stable reasons for hard eligibility rejection."""

    WRONG_USER = "wrong_user"
    UNAUTHORIZED_SCOPE = "unauthorized_scope"
    INVALID_STATE = "invalid_state"
    EXPLICIT_TYPE_VIOLATION = "explicit_type_violation"
    HARD_TIME_VIOLATION = "hard_time_violation"
    RECORD_MISSING = "record_missing"
    CORRUPT_RECORD = "corrupt_record"
    DOMAIN_CONSTRAINT_VIOLATION = "domain_constraint_violation"


class RecallErrorCode(StrEnum):
    """Fatal, non-degradable Recall error codes."""

    REQUEST_INVALID = "request_invalid"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    AUTHORIZATION_UNAVAILABLE = "authorization_unavailable"
    RECORD_LOAD_FAILED = "record_load_failed"
    CONTRACT_VIOLATION = "contract_violation"
    OUTPUT_SCHEMA_INVALID = "output_schema_invalid"
    CONCURRENT_MODIFICATION = "concurrent_modification"


# ---------------------------------------------------------------------------
# Public request / result contracts
# ---------------------------------------------------------------------------


class RecallRequest(FrozenModel):
    """External Recall request describing the call site.

    Identity, authorization, hard budgets and explicit limits are controlled
    by deterministic code; the intent model must not infer or override them.
    """

    MAX_RECENT_MESSAGES: ClassVar[int] = 50
    MAX_EVIDENCE_ITEMS_LIMIT: ClassVar[int] = 50
    MAX_CONTEXT_TOKENS_LIMIT: ClassVar[int] = 8000

    user_id: str = Field(min_length=1)
    agent_id: str | None = None
    session_id: str | None = None
    query: str = Field(min_length=1)
    recent_messages: tuple[Message, ...] = ()
    purpose: str | None = None
    explicit_types: tuple[MemoryType, ...] = ()
    explicit_time_range: TemporalAnchor | None = None
    temporal_intent: TemporalIntent | None = None
    allow_agent_history: bool = True
    allow_user_shared: bool = True
    max_evidence_items: int = Field(default=10, ge=1)
    max_context_tokens: int = Field(default=2000, ge=1)
    diagnostic: bool = False

    @field_validator("query")
    @classmethod
    def _nonblank_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("recent_messages")
    @classmethod
    def _bounded_recent_messages(cls, value: tuple[Message, ...]) -> tuple[Message, ...]:
        if len(value) > cls.MAX_RECENT_MESSAGES:
            raise ValueError(f"recent_messages exceed hard limit {cls.MAX_RECENT_MESSAGES}")
        return value

    @model_validator(mode="after")
    def _budget_within_hard_limits(self) -> "RecallRequest":
        if self.max_evidence_items > self.MAX_EVIDENCE_ITEMS_LIMIT:
            raise ValueError("max_evidence_items exceeds system hard limit")
        if self.max_context_tokens > self.MAX_CONTEXT_TOKENS_LIMIT:
            raise ValueError("max_context_tokens exceeds system hard limit")
        return self


class RecallMetadata(FrozenModel):
    """Structured metadata describing how Recall executed."""

    intent_summary: str = ""
    lanes_used: tuple[RecallLane, ...] = ()
    candidate_count: int = Field(default=0, ge=0)
    filtered_count: int = Field(default=0, ge=0)
    final_count: int = Field(default=0, ge=0)
    sufficiency: Sufficiency
    execution_status: ExecutionStatus
    degradations: tuple[DegradationCode, ...] = ()
    budget_truncation: bool = False
    pipeline_version: str = "0.1.0"
    stage_timings: tuple[tuple[str, float], ...] = ()
    diagnostics: tuple[str, ...] = ()


class EvidenceItem(FrozenModel):
    """A single traceable evidence entry rendered into context."""

    evidence_id: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    role: EvidenceRole
    content: str
    memory_type: MemoryType
    scope: MemoryScope
    occurred_at: datetime | None = None
    valid_from: datetime | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    importance: int = Field(default=1, ge=1, le=10)
    source: str = "unknown"
    relation_note: str = ""


class EvidenceGroup(FrozenModel):
    """Atomic evidence unit used for set selection.

    Conflict sides and necessary relation context must travel together; the
    selector never splits a group to fit budget.
    """

    group_id: str = Field(min_length=1)
    primary: EvidenceItem
    supporting: tuple[EvidenceItem, ...] = ()
    historical: tuple[EvidenceItem, ...] = ()
    conflicting: tuple[EvidenceItem, ...] = ()
    relations: tuple[MemoryRelation, ...] = ()
    resolution: str = ""
    roles: tuple[EvidenceRole, ...] = ()


class RecallResult(FrozenModel):
    """Public Recall result: structured evidence plus injectable context."""

    context: str
    evidence: tuple[EvidenceGroup, ...] = ()
    metadata: RecallMetadata


# ---------------------------------------------------------------------------
# Internal stage contracts
# ---------------------------------------------------------------------------


class QueryVariant(FrozenModel):
    """A single recall query string together with its generation purpose."""

    text: str = Field(min_length=1)
    purpose: str = ""


class ExplicitConstraints(FrozenModel):
    """Constraints the caller asserted explicitly; never overridden by LLM."""

    types: tuple[MemoryType, ...] = ()
    time_range: TemporalAnchor | None = None
    temporal_intent: TemporalIntent | None = None


class RecallIntent(FrozenModel):
    """Structured expression of "what memory is needed"."""

    primary_query: str = Field(min_length=1)
    purpose: str = "general_recovery"
    query_variants: tuple[QueryVariant, ...] = ()
    target_memory_types: tuple[MemoryType, ...] = (
        MemoryType.FACT,
        MemoryType.EVENT,
    )
    temporal_need: TemporalIntent | None = None
    subject_hints: tuple[str, ...] = ()
    relationship_need: bool = False
    explicit_constraints: ExplicitConstraints = Field(default_factory=ExplicitConstraints)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertain: tuple[str, ...] = ()
    fallback: bool = False


class LanePlan(FrozenModel):
    """Per-lane authorization-aware retrieval plan."""

    lane: RecallLane
    enabled: bool
    scope: MemoryScope
    query_variants: tuple[QueryVariant, ...] = ()
    target_types: tuple[MemoryType, ...] = ()
    candidate_quota: int = Field(ge=0)
    temporal_need: TemporalIntent | None = None
    relation_expansion: bool = False


class RecallPlan(FrozenModel):
    """Deterministic plan: which lanes, quotas and budgets are authorized."""

    intent: RecallIntent
    lanes: tuple[LanePlan, ...]
    global_candidate_limit: int = Field(ge=0)
    relation_expansion_depth: int = Field(default=1, ge=0)
    deadline_at: datetime | None = None


class HitSignal(FrozenModel):
    """Where and how a candidate was hit across lanes and paths."""

    lane: RecallLane
    path: str = "semantic"
    query_variant: str | None = None
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrievedCandidate(FrozenModel):
    """A memory id surfaced by retrieval, before SQLite hydration."""

    memory_id: str = Field(min_length=1)
    hits: tuple[HitSignal, ...] = ()


class EligibleCandidate(FrozenModel):
    """A candidate that passed hard eligibility filtering."""

    memory_id: str = Field(min_length=1)
    record: MemoryRecord
    hits: tuple[HitSignal, ...] = ()
    temporal_role: TemporalIntent | None = None


class RejectedCandidate(FrozenModel):
    """A candidate dropped by hard filtering, kept for diagnostics only."""

    memory_id: str = Field(min_length=1)
    reason: RejectionReason
    detail: str = ""


class ScoreComponent(FrozenModel):
    """A single normalized, explained scoring component."""

    name: str = Field(min_length=1)
    value: float = Field(ge=0.0, le=1.0)
    explanation: str = ""


class ScoredCandidate(FrozenModel):
    """An eligible candidate with utility score and provisional evidence role."""

    memory_id: str = Field(min_length=1)
    record: MemoryRecord
    hits: tuple[HitSignal, ...] = ()
    components: tuple[ScoreComponent, ...] = ()
    utility: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    scoring_fallback: bool = False
    provisional_role: EvidenceRole | None = None


class ContextAssembly(FrozenModel):
    """Output of the context assembler stage.

    Carries the rendered context plus the metadata fields only the assembler
    can decide (sufficiency, intent summary, lanes actually used).
    """

    context: str
    sufficiency: Sufficiency
    intent_summary: str = ""
    lanes_used: tuple[RecallLane, ...] = ()

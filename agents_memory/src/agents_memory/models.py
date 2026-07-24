from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class MemoryType(StrEnum):
    FACT = "fact"
    EVENT = "event"


class SourceKind(StrEnum):
    USER_EXPLICIT = "user_explicit"
    USER_CONFIRMED = "user_confirmed"
    TOOL_VERIFIED = "tool_verified"


class Validity(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class RelationKind(StrEnum):
    DUPLICATE = "duplicate"
    SUPPLEMENT = "supplement"
    CONTRADICT = "contradict"
    CORRECT = "correct"
    NONE = "none"
    SUPERSEDES = "supersedes"
    CORRECTS = "corrects"


class Action(StrEnum):
    ADD = "add"
    UPDATE = "update"
    NOOP = "noop"
    DEFER = "defer"
    REJECT = "reject"


class WriteStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRYABLE = "retryable"


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    EXTRACTION_FAILED = "extraction_failed"
    RELATION_FAILED = "relation_failed"
    AMBIGUOUS_RELATION = "ambiguous_relation"
    STORAGE_FAILED = "storage_failed"
    INDEX_UNAVAILABLE = "index_unavailable"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    REQUEST_IN_PROGRESS = "request_in_progress"
    NOT_FOUND = "not_found"


class IndexOperationKind(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


class IndexOperationStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class TemporalGranularity(StrEnum):
    UNKNOWN = "unknown"
    YEAR = "year"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    HOUR = "hour"


class TemporalResolution(StrEnum):
    RESOLVED = "resolved"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"


class EventStatus(StrEnum):
    UNKNOWN = "unknown"
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EventIdentity(StrEnum):
    SAME_EVENT = "same_event"
    DIFFERENT_EVENT = "different_event"
    UNKNOWN = "unknown"


class TemporalRelation(StrEnum):
    SAME_WINDOW = "same_window"
    BEFORE = "before"
    AFTER = "after"
    OVERLAP = "overlap"
    UNKNOWN = "unknown"


class PendingResolutionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    OBSOLETE = "obsolete"


class TemporalAnchor(FrozenModel):
    raw_text: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    granularity: TemporalGranularity = TemporalGranularity.UNKNOWN
    timezone: str | None = None
    certainty: str = "unknown"
    resolution: TemporalResolution = TemporalResolution.UNRESOLVED

    @model_validator(mode="after")
    def validate_interval(self) -> "TemporalAnchor":
        if (self.start is None) != (self.end is None):
            raise ValueError("temporal interval requires both start and end")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("temporal interval end must be after start")
        if (
            self.resolution is TemporalResolution.RESOLVED
            and self.start is None
        ):
            raise ValueError("resolved temporal anchor requires an interval")
        return self


class EventFrame(FrozenModel):
    actor: str = "unknown"
    predicate: str = "unknown"
    object: str = "unknown"
    location: str = "unknown"
    status: EventStatus = EventStatus.UNKNOWN
    polarity: str = "unknown"
    modality: str = "unknown"
    temporal_anchor: TemporalAnchor = Field(default_factory=TemporalAnchor)

    @field_validator(
        "actor", "predicate", "object", "location", "polarity", "modality"
    )
    @classmethod
    def nonblank_dimension(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("event dimensions must not be blank")
        return stripped


class Message(FrozenModel):
    message_id: str = Field(min_length=1)
    role: str = Field(pattern="^(user|assistant|tool|system)$")
    content: str = Field(min_length=1)
    occurred_at: datetime | None = None


class MemoryScope(FrozenModel):
    user_id: str = Field(min_length=1)
    agent_id: str | None = None
    session_id: str | None = None

    def as_key(self) -> tuple[str, str | None, str | None]:
        return self.user_id, self.agent_id, self.session_id


class CandidateMemory(FrozenModel):
    content: str = Field(min_length=1)
    type: MemoryType
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0, le=1)
    source_message_ids: tuple[str, ...] = Field(min_length=1)
    source_kind: SourceKind
    event_frame: EventFrame | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class MemoryRecord(FrozenModel):
    id: str = Field(min_length=1)
    scope: MemoryScope
    type: MemoryType
    content: str = Field(min_length=1)
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0, le=1)
    validity: Validity = Validity.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_from: datetime | None = None
    event_frame: EventFrame | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySource(FrozenModel):
    memory_id: str
    message_id: str
    role: str
    source_kind: SourceKind
    excerpt: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryRelation(FrozenModel):
    from_memory_id: str
    to_memory_id: str
    relation: RelationKind
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("relation")
    @classmethod
    def persistent_relation(cls, value: RelationKind) -> RelationKind:
        if value not in (RelationKind.SUPERSEDES, RelationKind.CORRECTS):
            raise ValueError("only supersedes/corrects are persistent")
        return value


class RelationMatch(FrozenModel):
    memory_id: str
    relation: RelationKind
    identity: EventIdentity | None = None
    temporal: TemporalRelation | None = None
    explicit_correction: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = ""
    similarity: float | None = Field(default=None, ge=0, le=1)


class CandidateResult(FrozenModel):
    candidate_index: int = Field(ge=0)
    content: str
    action: Action
    memory_id: str | None = None
    resolution_id: str | None = None
    resolution_status: PendingResolutionStatus | None = None
    replaced_memory_ids: tuple[str, ...] = ()
    matches: tuple[RelationMatch, ...] = ()
    missing_dimensions: tuple[str, ...] = ()
    reason: str = ""


class WriteReport(FrozenModel):
    request_id: str
    status: WriteStatus
    results: tuple[CandidateResult, ...] = ()
    extracted_count: int = Field(default=0, ge=0)
    filtered_count: int = Field(default=0, ge=0)
    sqlite_committed: bool = False
    index_synced: bool = False
    retryable: bool = False
    error_code: ErrorCode | None = None
    error_message: str | None = None


class PendingResolution(FrozenModel):
    id: str = Field(min_length=1)
    scope: MemoryScope
    candidate: CandidateMemory
    grouped_candidates: tuple[CandidateMemory, ...] = ()
    conflicting_memory_ids: tuple[str, ...] = ()
    identity: EventIdentity = EventIdentity.UNKNOWN
    temporal_relation: TemporalRelation = TemporalRelation.UNKNOWN
    semantic_relation: RelationKind
    missing_dimensions: tuple[str, ...] = ()
    reason: str = ""
    source_message_ids: tuple[str, ...] = ()
    source_messages: tuple[Message, ...] = ()
    processed_evidence_message_ids: tuple[str, ...] = ()
    importance: int = Field(ge=1, le=10)
    status: PendingResolutionStatus = PendingResolutionStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_evaluated_at: datetime | None = None
    expires_at: datetime


class ActionPlan(FrozenModel):
    candidate_index: int
    candidate: CandidateMemory
    action: Action
    new_memory_id: str | None = None
    target_ids: tuple[str, ...] = ()
    matches: tuple[RelationMatch, ...] = ()
    relation: RelationKind | None = None
    pending_resolution: PendingResolution | None = None
    reason: str = ""


class IndexOperation(FrozenModel):
    id: int | None = None
    request_id: str
    memory_id: str
    kind: IndexOperationKind
    status: IndexOperationStatus = IndexOperationStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

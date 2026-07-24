from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Message(FrozenModel):
    message_id: str = Field(min_length=1)
    role: str = Field(pattern="^(user|assistant|tool|system)$")
    content: str = Field(min_length=1)


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
    reason: str = ""
    similarity: float | None = Field(default=None, ge=0, le=1)


class CandidateResult(FrozenModel):
    candidate_index: int = Field(ge=0)
    content: str
    action: Action
    memory_id: str | None = None
    replaced_memory_ids: tuple[str, ...] = ()
    matches: tuple[RelationMatch, ...] = ()
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


class ActionPlan(FrozenModel):
    candidate_index: int
    candidate: CandidateMemory
    action: Action
    new_memory_id: str | None = None
    target_ids: tuple[str, ...] = ()
    matches: tuple[RelationMatch, ...] = ()
    relation: RelationKind | None = None
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

"""Durable value objects for adaptive, per-seed research loops.

The model-facing action contract is intentionally smaller than the runtime
record. A model can propose one semantic action, while identities, idempotency
keys, lease data, counters and persistence state remain runtime-owned.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.evidence import Usage


class ResearchActionKind(StrEnum):
    QUERY = "query"
    ADD_DIRECTION = "add_direction"
    STOP_REQUEST = "stop_request"


class QueryAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[ResearchActionKind.QUERY] = ResearchActionKind.QUERY
    direction_id: str = Field(min_length=1, max_length=200)
    capability_kind: CapabilityKind
    query: str = Field(min_length=1, max_length=4_000)
    rationale: str = Field(min_length=1, max_length=2_000)


class AddDirectionAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[ResearchActionKind.ADD_DIRECTION] = ResearchActionKind.ADD_DIRECTION
    parent_direction_id: str = Field(min_length=1, max_length=200)
    hint: str = Field(min_length=1, max_length=4_000)
    rationale: str = Field(min_length=1, max_length=2_000)


class StopRequestAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[ResearchActionKind.STOP_REQUEST] = ResearchActionKind.STOP_REQUEST
    reason: str = Field(min_length=1, max_length=2_000)
    supporting_evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    unresolved_questions: tuple[str, ...] = Field(default_factory=tuple, max_length=50)


ResearchAction = Annotated[
    QueryAction | AddDirectionAction | StopRequestAction,
    Field(discriminator="kind"),
]


class ResearchActionEnvelope(BaseModel):
    """Runtime-bound identity around one model-proposed semantic action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    plan_version: int = Field(ge=1)
    task_id: str
    loop_id: str
    step_id: str
    action: ResearchAction


class ResearchLoopStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    FAILED = "failed"

    @property
    def is_closed(self) -> bool:
        return self is not ResearchLoopStatus.ACTIVE


class ResearchStepStatus(StrEnum):
    DECIDING = "deciding"
    PREPARED = "prepared"
    ACCEPTED = "accepted"
    FAILED = "failed"
    FENCED = "fenced"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ResearchStepStatus.ACCEPTED,
            ResearchStepStatus.FAILED,
            ResearchStepStatus.FENCED,
        }


class EvidenceDigest(BaseModel):
    """Bounded and explicitly untrusted projection exposed to the agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    source_kind: str
    digest: str = Field(max_length=2_000)
    is_untrusted: bool = True


class ResearchDirectionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction_id: str
    parent_direction_id: str | None = None
    text: str = Field(max_length=1_000)
    capability_scope: tuple[CapabilityKind, ...] = Field(default_factory=tuple)


class ResearchLoopView(BaseModel):
    """Read-only, bounded decision context for :class:`ResearchAgent`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    plan_version: int
    task_id: str
    loop_id: str
    step_id: str
    objective: str = Field(max_length=4_000)
    seed: str = Field(max_length=2_000)
    directions: tuple[ResearchDirectionView, ...] = Field(default_factory=tuple, max_length=50)
    evidence: tuple[EvidenceDigest, ...] = Field(default_factory=tuple, max_length=100)
    coverage: tuple[CapabilityKind, ...] = Field(default_factory=tuple)
    remaining_steps: int = Field(ge=0)
    remaining_directions: int = Field(ge=0)
    remaining_tokens: int | None = Field(default=None, ge=0)
    remaining_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))


class ResearchAgentDecision(BaseModel):
    """One provider response plus actual reasoning usage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ResearchAction
    usage: Usage = Field(default_factory=Usage)


@runtime_checkable
class ResearchAgent(Protocol):
    async def decide(
        self, view: ResearchLoopView, *, decision_request_id: str
    ) -> ResearchAgentDecision: ...


class ResearchLoop(BaseModel):
    """Aggregate summary for one Plan seed Task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    loop_id: str
    run_id: str
    plan_version: int = Field(ge=1)
    task_id: str
    status: ResearchLoopStatus = ResearchLoopStatus.ACTIVE
    next_step_index: int = Field(default=0, ge=0)
    accepted_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    coverage: tuple[CapabilityKind, ...] = Field(default_factory=tuple)
    step_count: int = Field(default=0, ge=0)
    direction_count: int = Field(default=0, ge=0)
    usage: Usage = Field(default_factory=Usage)
    degradation_reason: str | None = None
    state_version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class ResearchDirection(BaseModel):
    """Sanitized loop-local direction, unique by focus hash per loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction_id: str
    loop_id: str
    run_id: str
    plan_version: int = Field(ge=1)
    task_id: str
    parent_direction_id: str | None = None
    text: str = Field(min_length=1, max_length=1_200)
    focus_hash: str = Field(min_length=1, max_length=128)
    capability_scope: tuple[CapabilityKind, ...] = Field(default_factory=tuple)
    source_step_id: str | None = None
    created_at: datetime


class ResearchStep(BaseModel):
    """One durable logical decision/capability boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    loop_id: str
    run_id: str
    plan_version: int = Field(ge=1)
    task_id: str
    step_index: int = Field(ge=0)
    status: ResearchStepStatus
    decision_request_id: str
    capability_request_id: str
    attempt_id: str
    lease_epoch: int = Field(ge=1)
    state_version_at_dispatch: int = Field(ge=1)
    action: ResearchActionEnvelope | None = None
    reasoning_reservation: Usage = Field(default_factory=Usage)
    capability_reservation: Usage = Field(default_factory=Usage)
    reasoning_usage: Usage = Field(default_factory=Usage)
    capability_usage: Usage = Field(default_factory=Usage)
    retry_count: int = Field(default=0, ge=0)
    accepted_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    result_operation_id: str | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime

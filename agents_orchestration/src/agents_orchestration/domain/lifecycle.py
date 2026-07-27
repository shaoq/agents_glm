"""Durable lifecycle records: Lease, Gate, Checkpoint (design Decision 4/11).

All three participate in atomic transactions alongside the State Version so that
restart recovery, at-most-once Gate consumption and semantic checkpoints are
restored exactly (tasks 3.6 / 4.5 / 9.5).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.enums import GateState, GateType
from agents_orchestration.domain.ids import AttemptId, CheckpointId, GateId, RunId, TaskId


class LeaseState(StrEnum):
    """Lease lifecycle. Epoch is monotonic per Task (design Decision 3 / task 4.2)."""

    CLAIMED = "claimed"
    RENEWED = "renewed"
    RELEASED = "released"
    EXPIRED = "expired"

    @property
    def is_active(self) -> bool:
        return self in {LeaseState.CLAIMED, LeaseState.RENEWED}


class Lease(BaseModel):
    """An execution claim on a Task for one Attempt."""

    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    attempt_id: AttemptId
    run_id: RunId
    epoch: int = Field(ge=1)
    state: LeaseState = LeaseState.CLAIMED
    claimed_at: datetime
    expires_at: datetime
    released_at: datetime | None = None

    def renew(self, new_expires_at: datetime, at: datetime) -> Lease:
        return self.model_copy(
            update={
                "state": LeaseState.RENEWED,
                "expires_at": new_expires_at,
                "claimed_at": at,
            }
        )

    def release(self, at: datetime) -> Lease:
        return self.model_copy(update={"state": LeaseState.RELEASED, "released_at": at})

    def expire(self) -> Lease:
        return self.model_copy(update={"state": LeaseState.EXPIRED})

    def is_expired(self, now: datetime) -> bool:
        return self.state is not LeaseState.EXPIRED and now >= self.expires_at


class Gate(BaseModel):
    """A version-bound, single-use Human Gate (design Decision 11)."""

    model_config = ConfigDict(frozen=True)

    gate_id: GateId
    run_id: RunId
    gate_type: GateType
    actor: str
    role: str
    scope: str
    state_version: int
    plan_version: int | None = None
    task_id: TaskId | None = None
    artifact_hash: str | None = None
    allowed_response_schema: str
    expires_at: datetime
    state: GateState = GateState.OPEN
    response_request_id: str | None = None
    response_payload: dict | None = None
    responded_by: str | None = None
    responded_at: datetime | None = None
    consumed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.state is GateState.OPEN

    @property
    def is_consumed(self) -> bool:
        return self.state is GateState.CONSUMED

    def is_expired(self, now: datetime) -> bool:
        return self.state is GateState.OPEN and now >= self.expires_at

    def respond(
        self,
        *,
        request_id: str,
        actor: str,
        payload: dict,
        at: datetime,
    ) -> Gate:
        return self.model_copy(
            update={
                "state": GateState.RESPONDED,
                "response_request_id": request_id,
                "response_payload": payload,
                "responded_by": actor,
                "responded_at": at,
            }
        )

    def consume(self, at: datetime) -> Gate:
        return self.model_copy(update={"state": GateState.CONSUMED, "consumed_at": at})

    def cancel(self) -> Gate:
        return self.model_copy(update={"state": GateState.CANCELED})

    def expire(self) -> Gate:
        return self.model_copy(update={"state": GateState.EXPIRED})


class CheckpointKind(StrEnum):
    """Semantic checkpoint boundaries (design Decision 4 / task 4.5)."""

    PLAN = "plan"
    BRANCH_RESULT = "branch_result"
    GATE = "gate"
    RETRY = "retry"
    REPLAN = "replan"
    FINALIZATION = "finalization"


class Checkpoint(BaseModel):
    """A semantic recovery closure recorded atomically with state."""

    model_config = ConfigDict(frozen=True)

    checkpoint_id: CheckpointId
    run_id: RunId
    kind: CheckpointKind
    state_version: int
    plan_version: int | None = None
    artifact_hash: str | None = None
    reason: str
    created_at: datetime

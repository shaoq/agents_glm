"""Core execution records: Run, Task, Attempt, Operation (design Decision 3).

These are immutable value objects. The runtime mutates state by producing new
instances via ``model_copy``; persistence (Section 3) writes the resulting
snapshot within a compare-and-set transaction keyed on ``state_version``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.artifact import ArtifactRef
from agents_orchestration.domain.enums import (
    AttemptAcceptance,
    AttemptState,
    BranchRole,
    CapabilityKind,
    FailureCode,
    RunState,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.goal import CompletionContract, GoalSpec
from agents_orchestration.domain.ids import (
    AttemptId,
    OperationId,
    RunId,
    TaskId,
)
from agents_orchestration.domain.policy import Budget, RunPolicy


class OutcomeCertainty(StrEnum):
    """Whether an Operation's outcome is safely confirmable (design Risks)."""

    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class Run(BaseModel):
    """A single goal lifecycle. Resume never changes ``run_id``."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    raw_goal: str
    goal_clarification: str | None = None
    state: RunState = RunState.CREATED
    goal: GoalSpec | None = None
    completion: CompletionContract | None = None
    policy: RunPolicy
    budget: Budget = Field(default_factory=Budget)
    current_plan_version: int | None = None
    replan_count: int = Field(default=0, ge=0)
    revision_count: int = Field(default=0, ge=0)
    termination: TerminationReason | None = None
    paused_from_state: RunState | None = None
    state_version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def effective_goal(self) -> str:
        """The raw goal augmented with any user clarification (task 2.2).

        Without a clarification this is the raw goal verbatim; ``raw_goal`` is
        never mutated, so the original input stays auditable.
        """

        if self.goal_clarification:
            return f"{self.raw_goal}\n\nUser clarification:\n{self.goal_clarification}"
        return self.raw_goal

    def bump_version(self, at: datetime) -> Run:
        """Same-state resume generation bump (task 2.3): state unchanged,
        ``state_version`` advances exactly once, ``updated_at`` refreshes."""

        return self.model_copy(
            update={"state_version": self.state_version + 1, "updated_at": at}
        )

    def transition(self, to: RunState, at: datetime) -> Run:
        return self.model_copy(
            update={"state": to, "updated_at": at, "state_version": self.state_version + 1}
        )

    def terminate(self, reason: TerminationReason, at: datetime) -> Run:
        terminal = (
            RunState.SUCCEEDED
            if reason is TerminationReason.COMPLETED
            else RunState.FAILED
            if reason is not TerminationReason.CANCELED
            else RunState.CANCELED
        )
        return self.model_copy(
            update={
                "state": terminal,
                "termination": reason,
                "updated_at": at,
                "state_version": self.state_version + 1,
            }
        )

    def consume_budget(self, budget: Budget, at: datetime) -> Run:
        return self.model_copy(
            update={"budget": budget, "updated_at": at, "state_version": self.state_version + 1}
        )


class Task(BaseModel):
    """Stable business work. Retry does not change ``task_id``."""

    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    run_id: RunId
    plan_version: int
    worker_role: WorkerRole
    state: TaskState = TaskState.PENDING
    depth: int = Field(default=1, ge=1)
    depends_on: tuple[TaskId, ...] = Field(default_factory=tuple)
    required_capabilities: tuple[CapabilityKind, ...] = Field(default_factory=tuple)
    branch_role: BranchRole | None = None
    deliverable_path: str | None = None
    description: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    accepted_attempt_id: AttemptId | None = None
    failure_code: FailureCode | None = None
    last_error: str | None = None
    state_version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def is_ready_candidate(self) -> bool:
        """A PENDING task whose dependencies are all accepted."""

        return self.state is TaskState.PENDING

    def transition(self, to: TaskState, at: datetime, **changes: object) -> Task:
        return self.model_copy(
            update={
                "state": to,
                "updated_at": at,
                "state_version": self.state_version + 1,
                **changes,
            }
        )


class Attempt(BaseModel):
    """One execution of a Task. A new Attempt is created for every retry/resume."""

    model_config = ConfigDict(frozen=True)

    attempt_id: AttemptId
    task_id: TaskId
    run_id: RunId
    worker_role: WorkerRole
    lease_epoch: int
    plan_version: int
    state_version_at_dispatch: int
    state: AttemptState = AttemptState.DISPATCHED
    result_ref: ArtifactRef | None = None
    acceptance: AttemptAcceptance | None = None
    failure_code: FailureCode | None = None
    started_at: datetime
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    def succeed(self, result: ArtifactRef, at: datetime) -> Attempt:
        return self.model_copy(
            update={
                "state": AttemptState.SUCCEEDED,
                "result_ref": result,
                "acceptance": AttemptAcceptance.ACCEPTED,
                "finished_at": at,
            }
        )

    def fail(
        self, code: FailureCode, at: datetime, acceptance: AttemptAcceptance | None = None
    ) -> Attempt:
        return self.model_copy(
            update={
                "state": AttemptState.FAILED,
                "failure_code": code,
                "acceptance": acceptance,
                "finished_at": at,
            }
        )


class Operation(BaseModel):
    """One external Capability Call, for dedup, diagnostics and unknown results."""

    model_config = ConfigDict(frozen=True)

    operation_id: OperationId
    attempt_id: AttemptId
    capability_id: str
    dedup_request_id: str
    outcome_certainty: OutcomeCertainty = OutcomeCertainty.UNKNOWN
    started_at: datetime
    finished_at: datetime | None = None

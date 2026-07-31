"""Typed enums for the orchestration domain.

Every formal state transition in the runtime is expressed through these enums so
that the deterministic state machine, events and persistence all share one
vocabulary (design Decision 2/3).
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    """Run lifecycle phases plus control and terminal states.

    Phase progression follows the fixed outer lifecycle (design Decision 8):
    normalize → plan → research → analyze → write → review → finalize.
    """

    CREATED = "created"
    NORMALIZING = "normalizing"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    RESEARCHING = "researching"
    ANALYZING = "analyzing"
    WRITING = "writing"
    REVIEWING = "reviewing"
    AWAITING_FINAL_REVIEW = "awaiting_final_review"
    FINALIZING = "finalizing"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED}

    @property
    def is_gate_waiting(self) -> bool:
        return self in {RunState.AWAITING_PLAN_APPROVAL, RunState.AWAITING_FINAL_REVIEW}


class TaskState(StrEnum):
    """Task lifecycle. Retry and Replan never change the Task identity."""

    PENDING = "pending"
    READY = "ready"
    DISPATCHED = "dispatched"
    AWAITING_RETRY = "awaiting_retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    SKIPPED = "skipped"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.SUPERSEDED,
            TaskState.SKIPPED,
            TaskState.CANCELED,
        }

    @property
    def is_accepted(self) -> bool:
        """A Task whose result is part of the accepted Run state."""

        return self is TaskState.SUCCEEDED


class AttemptState(StrEnum):
    """Per-execution attempt lifecycle."""

    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self is not AttemptState.DISPATCHED


class AttemptAcceptance(StrEnum):
    """Why an attempted result was accepted or rejected by the runtime."""

    ACCEPTED = "accepted"
    REJECTED_LATE = "rejected_late"
    REJECTED_STALE_LEASE = "rejected_stale_lease"
    REJECTED_STALE_PLAN = "rejected_stale_plan"
    REJECTED_STALE_STATE = "rejected_stale_state"
    REJECTED_SUPERSEDED = "rejected_superseded"


class GateType(StrEnum):
    """The four first-release Human Gates."""

    GOAL_CLARIFICATION = "goal_clarification"
    PLAN_APPROVAL = "plan_approval"
    CONFLICT_RESOLUTION = "conflict_resolution"
    FINAL_REVIEW = "final_review"


class GateState(StrEnum):
    """Gate lifecycle. A response is consumed at most once (design Decision 11)."""

    OPEN = "open"
    RESPONDED = "responded"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {GateState.CONSUMED, GateState.EXPIRED, GateState.CANCELED}


class CompletionState(StrEnum):
    """Deterministic Completion Contract evaluation outcome (task 10.6)."""

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class TerminationReason(StrEnum):
    """Why a Run reached a terminal state."""

    COMPLETED = "completed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    REQUIRED_EVIDENCE_MISSING = "required_evidence_missing"
    GATE_EXPIRED = "gate_expired"
    CANCELED = "canceled"
    FAILED = "failed"
    INTERNAL_ERROR = "internal_error"


class FailureCode(StrEnum):
    """Structured failure codes for capabilities and the runtime.

    ``retryable`` guides the durable Retry classifier (task 4.4); it must not be
    the only signal — the runtime also consults the retry budget and backoff.
    """

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    POLICY_VIOLATION = "policy_violation"
    BUDGET_EXCEEDED = "budget_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UPSTREAM_ERROR = "upstream_error"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        return self in {
            FailureCode.TIMEOUT,
            FailureCode.UNAVAILABLE,
            FailureCode.RATE_LIMITED,
            FailureCode.UPSTREAM_ERROR,
        }


class EffectType(StrEnum):
    """The kind of formal effect recorded as a Domain Event (task 2.9)."""

    RUN_STATE_TRANSITION = "run_state_transition"
    RUN_CREATED = "run_created"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_CANCELED = "run_canceled"
    RUN_TERMINATED = "run_terminated"
    GOAL_NORMALIZED = "goal_normalized"
    GOAL_CLARIFICATION_REQUESTED = "goal_clarification_requested"
    COMPLETION_AMENDED = "completion_amended"
    PLAN_PROPOSED = "plan_proposed"
    PLAN_ACCEPTED = "plan_accepted"
    PLAN_REJECTED = "plan_rejected"
    PLAN_REPLANNED = "plan_replanned"
    TASK_MATERIALIZED = "task_materialized"
    TASK_DISPATCHED = "task_dispatched"
    TASK_STATE_TRANSITION = "task_state_transition"
    TASK_SUPERSEDED = "task_superseded"
    ATTEMPT_CREATED = "attempt_created"
    ATTEMPT_ACCEPTED = "attempt_accepted"
    ATTEMPT_REJECTED = "attempt_rejected"
    LEASE_CLAIMED = "lease_claimed"
    LEASE_RELEASED = "lease_released"
    LEASE_EXPIRED = "lease_expired"
    CHECKPOINT_RECORDED = "checkpoint_recorded"
    GATE_OPENED = "gate_opened"
    GATE_RESPONDED = "gate_responded"
    GATE_CONSUMED = "gate_consumed"
    GATE_EXPIRED = "gate_expired"
    GATE_INVALIDATED = "gate_invalidated"
    EVIDENCE_JOINED = "evidence_joined"
    CAPABILITY_INVOKED = "capability_invoked"
    ARTIFACT_RECORDED = "artifact_recorded"
    BUDGET_CONSUMED = "budget_consumed"
    RESEARCH_LOOP_STARTED = "research_loop_started"
    RESEARCH_STEP_PREPARED = "research_step_prepared"
    RESEARCH_ACTION_ACCEPTED = "research_action_accepted"
    RESEARCH_ACTION_REJECTED = "research_action_rejected"
    RESEARCH_QUERY_ACCEPTED = "research_query_accepted"
    RESEARCH_DIRECTION_ADDED = "research_direction_added"
    RESEARCH_DIRECTION_DEDUPED = "research_direction_deduped"
    RESEARCH_STOP_REQUESTED = "research_stop_requested"
    RESEARCH_STOP_REJECTED = "research_stop_rejected"
    RESEARCH_LOOP_EXHAUSTED = "research_loop_exhausted"
    RESEARCH_LOOP_COMPLETED = "research_loop_completed"


class CapabilityKind(StrEnum):
    """First-release capabilities are all read-only."""

    MEMORY_RECALL = "memory_recall"
    RAG_SEARCH = "rag_search"
    WEB_RESEARCH = "web_research"
    MODEL = "model"


class WorkerRole(StrEnum):
    """The five first-release worker roles (design Decision 6)."""

    RESEARCH_PLANNER = "research_planner"
    EVIDENCE_RESEARCHER = "evidence_researcher"
    ANALYST = "analyst"
    REPORT_WRITER = "report_writer"
    REPORT_REVIEWER = "report_reviewer"


class BranchRole(StrEnum):
    """Research Branch roles used by Evidence Join (task 8.1)."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    CONDITIONAL = "conditional"
    ANY_OF = "any_of"
    QUORUM = "quorum"


class ReviewVerdict(StrEnum):
    """ReportReviewer proposals (task 10.3)."""

    PASS = "pass"
    REVISE = "revise"
    RESEARCH_GAP = "research_gap"
    CONFLICT = "conflict"
    ESCALATE = "escalate"


class Sufficiency(StrEnum):
    """Evidence Sufficiency states (task 8.7)."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class SufficiencyVerdict(StrEnum):
    """Strongly-typed result of an ANALYZE sufficiency review.

    L0 (structural) and L1 (semantic) funnels both emit this verdict so that
    phase acceptance never parses a free-text reason to decide routing
    (analyze-sufficiency-feedback Decision 2).
    """

    SUFFICIENT = "sufficient"
    RESEARCH_GAP = "research_gap"
    CONFLICT = "conflict"


class ReviewSource(StrEnum):
    """Which sufficiency funnel produced a ``SufficiencyReview``.

    ``STRUCTURAL`` is the deterministic L0 branch (zero required evidence);
    ``SEMANTIC`` is the L1 reviewer judging candidate Analysis vs. Evidence.
    """

    STRUCTURAL = "structural"
    SEMANTIC = "semantic"

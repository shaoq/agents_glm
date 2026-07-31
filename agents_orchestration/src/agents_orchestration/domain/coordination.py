"""Coordination domain contracts for the RunCoordinator (Ch.2 tasks 2.1-2.9).

Pure, deterministic models that govern how a Run advances one bounded semantic
step at a time. These contracts live in the domain layer — they depend only on
enums/value objects — and express the fixed phase routing, stage execution
fingerprinting, and deterministic result acceptance that the RunCoordinator
(Ch.4) and phase handlers (Ch.5-7) will obey.

Key invariant (task 2.9): a formal next Run state is selected ONLY from accepted
durable Run state, open Gate state, active Plan version, and persisted
continuation data. Model or evidence content can never choose or directly
commit a Run state — it only produces proposals that deterministic acceptance
rules consume.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.artifact import ArtifactRef
from agents_orchestration.domain.enums import FailureCode, GateType, RunState, WorkerRole
from agents_orchestration.domain.ids import RunId
from agents_orchestration.domain.lifecycle import GateContinuation, GateContinuationIntent

# --- Task 2.1 / 2.2: advance disposition + report --------------------------


class AdvanceDisposition(StrEnum):
    """The strict outcome of one bounded coordinator advance (design Decision 2).

    Meanings are exhaustive and mutually exclusive:

    - ``PROGRESSED``: durable state, an accepted result, or formal work changed.
    - ``BLOCKED``: a known external condition prevents progress (open Gate or
      explicit Pause). Watch must stop.
    - ``IDLE``: no work completed this call, but the Run is not semantically
      blocked; Watch may poll again.
    - ``TERMINAL``: the Run is succeeded, failed, or canceled.
    """

    PROGRESSED = "progressed"
    BLOCKED = "blocked"
    IDLE = "idle"
    TERMINAL = "terminal"


class TaskTickSummary(BaseModel):
    """Deterministic snapshot of one ``TaskRuntimeTick`` used by AdvanceReport.

    The coordinator owns the Run lifecycle; the Task Runtime owns Task attempts.
    This summary carries only what the Watch loop needs to decide progress, so
    the coordination domain never imports the runtime layer.
    """

    model_config = ConfigDict(frozen=True)

    dispatched: int = Field(default=0, ge=0)
    accepted: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    terminal: bool = False


class AdvanceReport(BaseModel):
    """The result of ``RunCoordinator.advance(run_id)`` (design Decision 2).

    Immutable: every field recording what happened in this bounded step is
    frozen so reports can be persisted, logged, and compared without aliasing.
    """

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    from_state: RunState
    to_state: RunState
    disposition: AdvanceDisposition
    reason: str = ""
    state_version: int = Field(ge=1)
    stage_logical_key: str | None = None
    task_tick: TaskTickSummary | None = None
    continue_immediately: bool = True

    @property
    def progressed(self) -> bool:
        return self.disposition is AdvanceDisposition.PROGRESSED


# --- Task 2.3 / 2.4: deterministic RunState -> phase routing ---------------


class PhaseId(StrEnum):
    """The fixed set of coordinator phases (design Decision 3)."""

    INIT = "init"  # CREATED -> NORMALIZING
    GOAL = "goal"  # NORMALIZING
    PLAN = "plan"  # PLANNING
    RESEARCH = "research"  # RESEARCHING
    ANALYZE = "analyze"  # ANALYZING
    WRITE = "write"  # WRITING
    REVIEW = "review"  # REVIEWING
    FINALIZE = "finalize"  # FINALIZING


PHASE_FOR_STATE: dict[RunState, PhaseId | None] = {
    RunState.CREATED: PhaseId.INIT,
    RunState.NORMALIZING: PhaseId.GOAL,
    RunState.PLANNING: PhaseId.PLAN,
    RunState.RESEARCHING: PhaseId.RESEARCH,
    RunState.ANALYZING: PhaseId.ANALYZE,
    RunState.WRITING: PhaseId.WRITE,
    RunState.REVIEWING: PhaseId.REVIEW,
    RunState.FINALIZING: PhaseId.FINALIZE,
    RunState.PAUSED: None,
    RunState.AWAITING_PLAN_APPROVAL: None,
    RunState.AWAITING_FINAL_REVIEW: None,
    RunState.SUCCEEDED: None,
    RunState.FAILED: None,
    RunState.CANCELED: None,
}


def phase_for_state(state: RunState) -> PhaseId | None:
    """Select the eligible coordinator phase solely from durable Run state.

    Terminal, paused, and gate-waiting states have no active phase (task 2.3).
    This is the single source of truth for routing; it deliberately takes no
    model/evidence argument (task 2.9 invariant).
    """

    return PHASE_FOR_STATE[state]


# --- Task 6.1 / 6.2: phase-aware Worker role eligibility -------------------


PHASE_ROLES: dict[PhaseId, frozenset[WorkerRole]] = {
    PhaseId.RESEARCH: frozenset({WorkerRole.EVIDENCE_RESEARCHER}),
}
# ANALYZE / WRITE / REVIEW are coordinator-owned phase ports, not dispatched
# phases (remove-noop-phase-tasks 2.2). They intentionally have no entry here so
# eligible_worker_roles returns an explicit empty set for them rather than
# implying "any role may dispatch".


def eligible_worker_roles(state: RunState) -> frozenset[WorkerRole] | None:
    """Worker roles permitted to dispatch for ``state``'s phase.

    Returns the phase's eligible roles for RESEARCHING; an explicit empty set for
    active non-Task phases (ANALYZE/WRITE/REVIEW coordinator-owned ports, plus
    Goal/Plan/Finalize); and ``None`` only for non-active states (paused /
    gate-waiting / terminal). ``None`` is never an implicit "dispatch nothing":
    active phases always get a concrete (possibly empty) set
    (remove-noop-phase-tasks 2.2).
    """

    phase = phase_for_state(state)
    if phase is None:
        return None
    return PHASE_ROLES.get(phase, frozenset())


# --- Task 2.5 / 2.6: logical stage key + input fingerprint + StageExecution -


def stage_logical_key(phase: PhaseId, *, context: str = "") -> str:
    """Stable logical stage key within a Run (task 2.5).

    Two executions share a logical key when they target the same phase and
    contextual slot (e.g. one focused Replan iteration vs. the original plan).
    """

    return f"{phase.value}:{context}" if context else phase.value


class InputFingerprint(BaseModel):
    """Immutable binding of a stage execution to its exact input versions and
    artifact hashes (task 2.5 / 2.6). Drives idempotency and stale detection.
    """

    model_config = ConfigDict(frozen=True)

    state_version: int = Field(ge=1)
    plan_version: int | None = None
    contract_version: int | None = None
    input_artifact_hashes: tuple[str, ...] = ()

    def hexdigest(self) -> str:
        """Deterministic SHA-256 of the bound versions + artifact hashes.

        Order-independent (sorted keys, stable tuple order) so equal inputs always
        produce equal fingerprints.
        """

        payload = json.dumps(
            {
                "state_version": self.state_version,
                "plan_version": self.plan_version,
                "contract_version": self.contract_version,
                "input_artifact_hashes": list(self.input_artifact_hashes),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class StageStatus(StrEnum):
    """StageExecution lifecycle (design Decision 5)."""

    PREPARED = "prepared"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    SUPERSEDED = "superseded"

    @property
    def is_accepted(self) -> bool:
        return self is StageStatus.ACCEPTED


class StageExecution(BaseModel):
    """Durable record of one phase execution's prepare/accept lifecycle
    (design Decision 5). Large payloads stay in immutable Artifacts; this record
    stores only refs, hashes, versions, status, and idempotency metadata.
    """

    model_config = ConfigDict(frozen=True)

    stage_execution_id: str
    run_id: RunId
    phase: PhaseId
    logical_stage_key: str
    fingerprint: InputFingerprint
    status: StageStatus = StageStatus.PREPARED
    output_artifact_refs: tuple[ArtifactRef, ...] = ()
    output_entity_ids: tuple[str, ...] = ()
    attempt_count: int = Field(default=0, ge=0)
    failure_code: FailureCode | None = None
    counts_toward_idle_budget: bool = False
    idempotency_key: str
    created_at: datetime
    updated_at: datetime

    def transition(self, status: StageStatus, *, at: datetime, **changes: object) -> StageExecution:
        """Return a new StageExecution with an updated status (immutable)."""

        return self.model_copy(update={"status": status, "updated_at": at, **changes})


def stage_idempotency_key(run_id: RunId, logical_key: str, fingerprint_hex: str) -> str:
    """Stable idempotency key for a stage execution (design Decision 5).

    Replays of the same logical stage with the same input fingerprint collide
    on this key so the repository can reuse the accepted result instead of
    re-invoking the provider.
    """

    return f"{run_id}|{logical_key}|{fingerprint_hex}"


# --- Task 2.7 / 2.8: deterministic phase acceptance + stale classification -


@dataclass(frozen=True)
class CapturedVersions:
    """The Run/Plan/Contract versions captured before an external provider call.

    Phase acceptance compares this snapshot against the versions current at
    accept time; any drift means the result is stale (design Decision 4).
    """

    state_version: int
    plan_version: int | None = None
    contract_version: int | None = None


class PhaseResultClassification(StrEnum):
    """Deterministic classification of a phase result against current versions."""

    ACCEPT = "accept"
    STALE = "stale"


def classify_phase_result(
    captured: CapturedVersions, current: CapturedVersions
) -> PhaseResultClassification:
    """Deterministic phase-result acceptance (task 2.7 / 2.8).

    A result is ``ACCEPT`` only when the Run's current state, plan, and contract
    versions still match those captured before the external provider call.
    Otherwise the result is ``STALE`` — retained as an observation and never
    allowed to advance the Run. Content of the result (model output, evidence)
    is never an input to this function (task 2.9 invariant).
    """

    if captured == current:
        return PhaseResultClassification.ACCEPT
    return PhaseResultClassification.STALE


# --- Task 8.1 / 8.5 / 8.6: Gate continuation -------------------------------


GATE_CONTINUATION_NEXT: dict[GateType, dict[str, str]] = {
    GateType.GOAL_CLARIFICATION: {
        "clarified": RunState.NORMALIZING.value,
        "cancelled": RunState.CANCELED.value,
    },
    GateType.PLAN_APPROVAL: {
        "approved": RunState.RESEARCHING.value,
        "rejected": RunState.PLANNING.value,
    },
    GateType.CONFLICT_RESOLUTION: {
        "resolved": RunState.RESEARCHING.value,
        "escalated": RunState.FAILED.value,
    },
    GateType.FINAL_REVIEW: {
        "approved": RunState.FINALIZING.value,
        "changes": RunState.REVIEWING.value,
    },
}


def build_gate_continuation(
    gate_type: GateType,
    run,
    *,
    intent: GateContinuationIntent | None = None,
    feedback: str | None = None,
    correlation_id: str | None = None,
    artifact_hash: str | None = None,
) -> GateContinuation:
    """Construct the version-bound continuation for a Gate opened from ``run``'s
    current phase (task 8.3 / 8.6)."""
    phase = phase_for_state(run.state)
    return GateContinuation(
        origin_phase=phase.value if phase else run.state.value,
        bound_state_version=run.state_version,
        bound_plan_version=run.current_plan_version,
        bound_artifact_hash=artifact_hash,
        intent=intent,
        feedback=feedback,
        correlation_id=correlation_id,
        next_state_by_outcome=tuple(GATE_CONTINUATION_NEXT.get(gate_type, {}).items()),
    )


class ContinuationOutcome(StrEnum):
    """Discriminated result of resolving a Gate continuation (task 2.1).

    Meanings are exhaustive and mutually exclusive so the Application layer
    never infers intent from a returned Run object (design Decision 2):

    - ``APPLIED``: a legal transition to a different state.
    - ``SAME_STATE``: a legal resume that keeps the state but bumps the version.
    - ``MISSING_CONTINUATION``: the Gate has no persisted continuation.
    - ``STALE``: the bound state/plan version no longer matches the Run.
    - ``UNKNOWN_OUTCOME``: the outcome is not in the continuation mapping.
    - ``INVALID_TRANSITION``: the target state violates the Run state machine.
    """

    APPLIED = "applied"
    SAME_STATE = "same_state"
    MISSING_CONTINUATION = "missing_continuation"
    STALE = "stale"
    UNKNOWN_OUTCOME = "unknown_outcome"
    INVALID_TRANSITION = "invalid_transition"


@dataclass(frozen=True)
class ContinuationResolution:
    """The discriminated resolution plus the target state (when one exists)."""

    outcome: ContinuationOutcome
    target_state: RunState | None = None
    reason: str = ""


def resolve_gate_continuation(gate, run, outcome: str) -> ContinuationResolution:
    """Classify how a Gate's continuation applies to ``run`` (task 2.1).

    Pure and deterministic: reads only the persisted continuation and the Run's
    versions/state. The Application layer consumes the explicit result rather
    than guessing stale, same-state or invalid from a returned Run (design
    Decision 2). Callers never choose the target state (task 8.7) — it comes
    solely from the persisted continuation.
    """

    from agents_orchestration.domain.state_machine import (
        StateTransitionError,
        assert_run_transition,
    )

    cont = gate.continuation
    if cont is None:
        return ContinuationResolution(
            ContinuationOutcome.MISSING_CONTINUATION, reason="gate has no continuation"
        )
    if cont.bound_state_version != run.state_version:
        return ContinuationResolution(
            ContinuationOutcome.STALE, reason="bound state version drifted"
        )
    if cont.bound_plan_version is not None and cont.bound_plan_version != run.current_plan_version:
        return ContinuationResolution(
            ContinuationOutcome.STALE, reason="bound plan version drifted"
        )
    target = cont.next_state_for(outcome)
    if target is None:
        return ContinuationResolution(
            ContinuationOutcome.UNKNOWN_OUTCOME, reason=f"outcome '{outcome}' not mapped"
        )
    try:
        target_state = RunState(target)
    except ValueError:
        return ContinuationResolution(
            ContinuationOutcome.INVALID_TRANSITION,
            reason=f"unknown RunState target '{target}'",
        )
    if target_state is run.state:
        return ContinuationResolution(ContinuationOutcome.SAME_STATE, target_state=target_state)
    try:
        assert_run_transition(run.state, target_state)
    except StateTransitionError:
        return ContinuationResolution(
            ContinuationOutcome.INVALID_TRANSITION,
            target_state=target_state,
            reason=f"illegal transition {run.state.value}->{target_state.value}",
        )
    return ContinuationResolution(ContinuationOutcome.APPLIED, target_state=target_state)


def apply_gate_continuation(gate, run, outcome: str, now):
    """Deterministically apply the Gate's continuation (task 8.5, delegated in 2.1).

    Delegates to :func:`resolve_gate_continuation`. ``APPLIED`` transitions the
    Run; ``SAME_STATE`` bumps the version in place (task 2.3); every other
    resolution advances nothing and the original Run is returned unchanged.
    """

    resolution = resolve_gate_continuation(gate, run, outcome)
    if resolution.outcome is ContinuationOutcome.APPLIED:
        return run.transition(resolution.target_state, now)
    if resolution.outcome is ContinuationOutcome.SAME_STATE:
        return run.bump_version(now)
    return run


__all__ = [
    "AdvanceDisposition",
    "AdvanceReport",
    "CapturedVersions",
    "ContinuationOutcome",
    "ContinuationResolution",
    "GATE_CONTINUATION_NEXT",
    "InputFingerprint",
    "PHASE_FOR_STATE",
    "PHASE_ROLES",
    "PhaseId",
    "PhaseResultClassification",
    "StageExecution",
    "StageStatus",
    "TaskTickSummary",
    "apply_gate_continuation",
    "build_gate_continuation",
    "classify_phase_result",
    "eligible_worker_roles",
    "phase_for_state",
    "resolve_gate_continuation",
    "stage_idempotency_key",
    "stage_logical_key",
]

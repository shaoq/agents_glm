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
from agents_orchestration.domain.enums import FailureCode, RunState
from agents_orchestration.domain.ids import RunId

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

    @property
    def progressed(self) -> bool:
        return self.disposition is AdvanceDisposition.PROGRESSED


# --- Task 2.3 / 2.4: deterministic RunState -> phase routing ---------------


class PhaseId(StrEnum):
    """The fixed set of coordinator phases (design Decision 3)."""

    INIT = "init"            # CREATED -> NORMALIZING
    GOAL = "goal"            # NORMALIZING
    PLAN = "plan"            # PLANNING
    RESEARCH = "research"    # RESEARCHING
    ANALYZE = "analyze"      # ANALYZING
    WRITE = "write"          # WRITING
    REVIEW = "review"        # REVIEWING
    FINALIZE = "finalize"    # FINALIZING


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
    idempotency_key: str
    created_at: datetime
    updated_at: datetime

    def transition(
        self, status: StageStatus, *, at: datetime, **changes: object
    ) -> StageExecution:
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


__all__ = [
    "AdvanceDisposition",
    "AdvanceReport",
    "CapturedVersions",
    "InputFingerprint",
    "PHASE_FOR_STATE",
    "PhaseId",
    "PhaseResultClassification",
    "StageExecution",
    "StageStatus",
    "TaskTickSummary",
    "classify_phase_result",
    "phase_for_state",
    "stage_idempotency_key",
    "stage_logical_key",
]

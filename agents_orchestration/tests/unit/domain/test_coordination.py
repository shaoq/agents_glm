"""Unit tests for the coordination domain contracts (Ch.2 tasks 2.1-2.9)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef
from agents_orchestration.domain.coordination import (
    PHASE_FOR_STATE,
    AdvanceDisposition,
    AdvanceReport,
    CapturedVersions,
    InputFingerprint,
    PhaseId,
    PhaseResultClassification,
    StageExecution,
    StageStatus,
    TaskTickSummary,
    classify_phase_result,
    phase_for_state,
    stage_idempotency_key,
    stage_logical_key,
)
from agents_orchestration.domain.enums import FailureCode, RunState

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _fingerprint(
    *,
    state_version: int = 1,
    plan_version: int | None = None,
    contract_version: int | None = None,
    hashes: tuple[str, ...] = (),
) -> InputFingerprint:
    return InputFingerprint(
        state_version=state_version,
        plan_version=plan_version,
        contract_version=contract_version,
        input_artifact_hashes=hashes,
    )


def _artifact(aid: str = "a1") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=aid,
        content_hash="sha256:abc",
        path=f"/p/{aid}",
        size_bytes=10,
        kind=ArtifactKind.ANALYSIS,
    )


def _stage(**over: object) -> StageExecution:
    base: dict[str, object] = {
        "stage_execution_id": "se-1",
        "run_id": "run-1",
        "phase": PhaseId.GOAL,
        "logical_stage_key": stage_logical_key(PhaseId.GOAL),
        "fingerprint": _fingerprint(),
        "idempotency_key": "k1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(over)
    return StageExecution(**base)  # type: ignore[arg-type]


# --- Task 2.1: AdvanceDisposition values -----------------------------------


@pytest.mark.unit
def test_advance_disposition_has_four_strict_values() -> None:
    assert {d.value for d in AdvanceDisposition} == {
        "progressed",
        "blocked",
        "idle",
        "terminal",
    }


@pytest.mark.unit
def test_advance_disposition_values_are_distinct_str_enum() -> None:
    assert AdvanceDisposition.PROGRESSED is not AdvanceDisposition.BLOCKED
    assert AdvanceDisposition("progressed") is AdvanceDisposition.PROGRESSED


# --- Task 2.2: immutable AdvanceReport -------------------------------------


@pytest.mark.unit
def test_advance_report_is_immutable_with_required_fields() -> None:
    report = AdvanceReport(
        run_id="run-1",
        from_state=RunState.CREATED,
        to_state=RunState.NORMALIZING,
        disposition=AdvanceDisposition.PROGRESSED,
        reason="created->normalizing",
        state_version=2,
    )
    assert report.task_tick is None
    assert report.progressed is True
    with pytest.raises(ValidationError):
        report.run_id = "run-2"  # type: ignore[misc]


@pytest.mark.unit
def test_advance_report_carries_optional_task_tick() -> None:
    report = AdvanceReport(
        run_id="run-1",
        from_state=RunState.RESEARCHING,
        to_state=RunState.RESEARCHING,
        disposition=AdvanceDisposition.IDLE,
        state_version=4,
        task_tick=TaskTickSummary(dispatched=0, accepted=0),
    )
    assert report.task_tick is not None
    assert report.task_tick.dispatched == 0
    assert report.progressed is False


# --- Task 2.3: deterministic RunState -> phase routing ----------------------


@pytest.mark.unit
def test_phase_for_state_routes_each_active_phase() -> None:
    assert phase_for_state(RunState.CREATED) is PhaseId.INIT
    assert phase_for_state(RunState.NORMALIZING) is PhaseId.GOAL
    assert phase_for_state(RunState.PLANNING) is PhaseId.PLAN
    assert phase_for_state(RunState.RESEARCHING) is PhaseId.RESEARCH
    assert phase_for_state(RunState.ANALYZING) is PhaseId.ANALYZE
    assert phase_for_state(RunState.WRITING) is PhaseId.WRITE
    assert phase_for_state(RunState.REVIEWING) is PhaseId.REVIEW
    assert phase_for_state(RunState.FINALIZING) is PhaseId.FINALIZE


@pytest.mark.unit
def test_phase_for_state_returns_none_for_non_active_states() -> None:
    for state in (
        RunState.PAUSED,
        RunState.AWAITING_PLAN_APPROVAL,
        RunState.AWAITING_FINAL_REVIEW,
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.CANCELED,
    ):
        assert phase_for_state(state) is None


# --- Task 2.4: phase identifiers + fixed routing table ---------------------


@pytest.mark.unit
def test_phase_routing_table_covers_every_run_state() -> None:
    assert set(PHASE_FOR_STATE) == set(RunState)


@pytest.mark.unit
def test_phase_routing_table_is_closed_over_phase_ids() -> None:
    phases = {p for p in PHASE_FOR_STATE.values() if p is not None}
    assert phases == set(PhaseId)


# --- Task 2.5: logical stage keys + input fingerprints ---------------------


@pytest.mark.unit
def test_stage_logical_key_distinguishes_context() -> None:
    assert stage_logical_key(PhaseId.RESEARCH) == "research"
    assert stage_logical_key(PhaseId.RESEARCH, context="plan_v3") == "research:plan_v3"
    assert stage_logical_key(PhaseId.RESEARCH, context="plan_v3") != stage_logical_key(
        PhaseId.RESEARCH, context="plan_v4"
    )


@pytest.mark.unit
def test_input_fingerprint_hexdigest_is_deterministic() -> None:
    a = _fingerprint(state_version=2, plan_version=3, contract_version=1, hashes=("h1", "h2"))
    b = _fingerprint(state_version=2, plan_version=3, contract_version=1, hashes=("h1", "h2"))
    assert a.hexdigest() == b.hexdigest()
    assert len(a.hexdigest()) == 64


@pytest.mark.unit
def test_input_fingerprint_changes_when_any_version_or_hash_drifts() -> None:
    base = _fingerprint(state_version=2, plan_version=3, contract_version=1, hashes=("h1",))
    drifted = (
        _fingerprint(state_version=3, plan_version=3, contract_version=1, hashes=("h1",)),
        _fingerprint(state_version=2, plan_version=4, contract_version=1, hashes=("h1",)),
        _fingerprint(state_version=2, plan_version=3, contract_version=2, hashes=("h1",)),
        _fingerprint(state_version=2, plan_version=3, contract_version=1, hashes=("h2",)),
    )
    for fp in drifted:
        assert fp.hexdigest() != base.hexdigest()


# --- Task 2.6: StageExecution status + version/hash binding ----------------


@pytest.mark.unit
def test_stage_execution_binds_fingerprint_and_status() -> None:
    stage = _stage(
        fingerprint=_fingerprint(state_version=5, plan_version=2, hashes=("ha",)),
        output_artifact_refs=(_artifact("a1"),),
    )
    assert stage.status is StageStatus.PREPARED
    assert stage.fingerprint.state_version == 5
    assert stage.output_artifact_refs[0].artifact_id == "a1"


@pytest.mark.unit
def test_stage_execution_transition_is_immutable() -> None:
    stage = _stage()
    accepted = stage.transition(StageStatus.ACCEPTED, at=NOW, output_entity_ids=("e1",))
    assert stage.status is StageStatus.PREPARED  # original unchanged
    assert accepted.status is StageStatus.ACCEPTED
    assert accepted.output_entity_ids == ("e1",)


@pytest.mark.unit
def test_stage_idempotency_key_collides_only_on_same_logical_key_and_fingerprint() -> None:
    fp = _fingerprint(state_version=2)
    assert stage_idempotency_key("run-1", "goal", fp.hexdigest()) == stage_idempotency_key(
        "run-1", "goal", fp.hexdigest()
    )
    assert stage_idempotency_key("run-1", "goal", fp.hexdigest()) != stage_idempotency_key(
        "run-1", "plan", fp.hexdigest()
    )
    other = _fingerprint(state_version=3)
    assert stage_idempotency_key("run-1", "goal", fp.hexdigest()) != stage_idempotency_key(
        "run-1", "goal", other.hexdigest()
    )


# --- Task 2.7 / 2.8: phase acceptance + stale classification ---------------


@pytest.mark.unit
def test_classify_phase_result_accepts_matching_versions() -> None:
    captured = CapturedVersions(state_version=2, plan_version=3, contract_version=1)
    assert classify_phase_result(captured, captured) is PhaseResultClassification.ACCEPT


@pytest.mark.unit
def test_classify_phase_result_marks_drift_as_stale() -> None:
    captured = CapturedVersions(state_version=2, plan_version=3, contract_version=1)
    for drifted in (
        CapturedVersions(state_version=3, plan_version=3, contract_version=1),
        CapturedVersions(state_version=2, plan_version=4, contract_version=1),
        CapturedVersions(state_version=2, plan_version=3, contract_version=2),
        CapturedVersions(state_version=2, plan_version=None, contract_version=1),
    ):
        assert classify_phase_result(captured, drifted) is PhaseResultClassification.STALE


@pytest.mark.unit
def test_classify_phase_result_has_no_content_argument() -> None:
    """Acceptance is decided solely from version snapshots; model output and
    evidence content are never an input (task 2.9 invariant)."""

    params = inspect.signature(classify_phase_result).parameters
    assert list(params) == ["captured", "current"]


# --- Task 2.9: model / evidence content cannot select a formal next state ---


@pytest.mark.unit
def test_phase_for_state_takes_only_durable_state() -> None:
    """Routing reads no model or evidence argument — only the Run state."""

    assert list(inspect.signature(phase_for_state).parameters) == ["state"]


@pytest.mark.unit
def test_model_control_instruction_cannot_alter_routing() -> None:
    """Even if a model/evidence payload 'instructs' a jump to FINALIZING, the
    deterministic routing table maps the current state to its fixed phase and
    ignores the instruction entirely (task 2.9)."""

    # Whatever a model might propose, routing depends only on RunState.
    assert phase_for_state(RunState.NORMALIZING) is PhaseId.GOAL
    assert phase_for_state(RunState.RESEARCHING) is PhaseId.RESEARCH
    # FINALIZING is reachable only by deterministic transitions, never by a
    # model instruction shortcut from an earlier phase.
    assert phase_for_state(RunState.NORMALIZING) is not PhaseId.FINALIZE


@pytest.mark.unit
def test_stage_failure_codes_are_known_categories() -> None:
    stage = _stage(failure_code=FailureCode.TIMEOUT).transition(StageStatus.FAILED, at=NOW)
    assert stage.failure_code is FailureCode.TIMEOUT
    assert StageStatus.ACCEPTED.is_accepted is True
    assert StageStatus.PREPARED.is_accepted is False

"""Unit tests for the typed Gate response contract (task 1.1-1.4)."""

from __future__ import annotations

import json

import pytest

from agents_orchestration.domain.enums import GateType
from agents_orchestration.orchestration.gate_responses import (
    GATE_RESPONSE_CONTRACT,
    GateResponseError,
    canonical_response_schema,
    outcomes_for,
    validate_gate_response,
)


def _expect_ok(gate_type: GateType, payload: dict, *, outcome: str, **fields: object) -> None:
    validated = validate_gate_response(gate_type, payload)
    assert validated.outcome == outcome
    for name, value in fields.items():
        assert getattr(validated, name) == value


# --- task 1.4: every legal outcome validates -------------------------------


@pytest.mark.unit
def test_contract_covers_four_gate_types() -> None:
    assert set(GATE_RESPONSE_CONTRACT) == set(GateType)


@pytest.mark.unit
def test_validate_each_legal_outcome() -> None:
    cases = [
        (
            GateType.GOAL_CLARIFICATION,
            {"outcome": "clarified", "clarification": "focus"},
            "clarified",
            {"clarification": "focus"},
        ),
        (GateType.GOAL_CLARIFICATION, {"outcome": "cancelled"}, "cancelled", {}),
        (
            GateType.GOAL_CLARIFICATION,
            {"outcome": "cancelled", "reason": "user"},
            "cancelled",
            {"reason": "user"},
        ),
        (GateType.PLAN_APPROVAL, {"outcome": "approved"}, "approved", {}),
        (
            GateType.PLAN_APPROVAL,
            {"outcome": "approved", "comment": "lgtm"},
            "approved",
            {"comment": "lgtm"},
        ),
        (
            GateType.PLAN_APPROVAL,
            {"outcome": "rejected", "feedback": "rewrite"},
            "rejected",
            {"feedback": "rewrite"},
        ),
        (
            GateType.CONFLICT_RESOLUTION,
            {"outcome": "resolved", "resolution": "pick A"},
            "resolved",
            {"resolution": "pick A"},
        ),
        (
            GateType.CONFLICT_RESOLUTION,
            {"outcome": "escalated", "reason": "stuck"},
            "escalated",
            {"reason": "stuck"},
        ),
        (GateType.FINAL_REVIEW, {"outcome": "approved"}, "approved", {}),
        (
            GateType.FINAL_REVIEW,
            {"outcome": "approved", "comment": "ok"},
            "approved",
            {"comment": "ok"},
        ),
        (
            GateType.FINAL_REVIEW,
            {"outcome": "changes", "feedback": "expand"},
            "changes",
            {"feedback": "expand"},
        ),
    ]
    for gate_type, payload, outcome, fields in cases:
        _expect_ok(gate_type, payload, outcome=outcome, **fields)


@pytest.mark.unit
def test_validate_only_declared_business_field_is_populated() -> None:
    validated = validate_gate_response(
        GateType.PLAN_APPROVAL, {"outcome": "rejected", "feedback": "redo"}
    )
    assert validated.feedback == "redo"
    # Other typed fields stay None even though the contract shares field names
    # across gate types.
    assert validated.clarification is None
    assert validated.resolution is None


@pytest.mark.unit
def test_outcomes_for_lists_legal_outcomes_per_gate() -> None:
    assert set(outcomes_for(GateType.GOAL_CLARIFICATION)) == {"clarified", "cancelled"}
    assert set(outcomes_for(GateType.FINAL_REVIEW)) == {"approved", "changes"}


# --- task 1.4: illegal payloads are rejected with a stable error -----------


@pytest.mark.unit
def test_validate_rejects_non_dict_payload() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.PLAN_APPROVAL, "not-an-object")  # type: ignore[arg-type]


@pytest.mark.unit
def test_validate_rejects_missing_outcome() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.PLAN_APPROVAL, {"comment": "x"})


@pytest.mark.unit
def test_validate_rejects_non_string_outcome() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.PLAN_APPROVAL, {"outcome": 1})


@pytest.mark.unit
def test_validate_rejects_unknown_outcome() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.PLAN_APPROVAL, {"outcome": "bogus"})


@pytest.mark.unit
def test_validate_rejects_legal_outcome_for_wrong_gate_type() -> None:
    # "approved" is legal for PLAN_APPROVAL/FINAL_REVIEW but not GOAL_CLARIFICATION.
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.GOAL_CLARIFICATION, {"outcome": "approved"})


@pytest.mark.unit
def test_validate_rejects_missing_required_business_field() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.GOAL_CLARIFICATION, {"outcome": "clarified"})


@pytest.mark.unit
def test_validate_rejects_blank_required_business_field() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(
            GateType.GOAL_CLARIFICATION, {"outcome": "clarified", "clarification": "   "}
        )


@pytest.mark.unit
def test_validate_rejects_wrong_typed_required_field() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(
            GateType.GOAL_CLARIFICATION, {"outcome": "clarified", "clarification": 5}
        )


@pytest.mark.unit
def test_validate_rejects_extra_undeclared_field() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.PLAN_APPROVAL, {"outcome": "approved", "bogus": 1})


@pytest.mark.unit
def test_validate_rejects_wrong_typed_optional_field() -> None:
    with pytest.raises(GateResponseError):
        validate_gate_response(GateType.GOAL_CLARIFICATION, {"outcome": "cancelled", "reason": 7})


# --- task 1.2: canonical schema derived from the same contract -------------


@pytest.mark.unit
def test_canonical_schema_is_valid_json_with_one_entry_per_outcome() -> None:
    schema = json.loads(canonical_response_schema(GateType.GOAL_CLARIFICATION))
    assert schema["type"] == "object"
    assert len(schema["oneOf"]) == 2
    enums = {branch["properties"]["outcome"]["enum"][0] for branch in schema["oneOf"]}
    assert enums == {"clarified", "cancelled"}


@pytest.mark.unit
def test_canonical_schema_marks_required_business_field() -> None:
    schema = json.loads(canonical_response_schema(GateType.PLAN_APPROVAL))
    rejected = next(
        b for b in schema["oneOf"] if b["properties"]["outcome"]["enum"] == ["rejected"]
    )
    assert "feedback" in rejected["required"]
    assert rejected["properties"]["feedback"] == {"type": "string", "minLength": 1}
    assert rejected["additionalProperties"] is False


@pytest.mark.unit
def test_canonical_schema_matches_runtime_contract() -> None:
    # The persisted schema enumerates exactly the outcomes the validator accepts.
    for gate_type in GateType:
        schema = json.loads(canonical_response_schema(gate_type))
        schema_outcomes = {branch["properties"]["outcome"]["enum"][0] for branch in schema["oneOf"]}
        assert schema_outcomes == set(outcomes_for(gate_type))

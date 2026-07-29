"""Typed, deterministic Human Gate response contracts (design Decision 1).

Each Gate type admits a fixed set of outcomes, and every outcome carries a
deterministic business-field contract. The canonical JSON Schema derived from
this contract is persisted on the Gate for display and audit; runtime
validation uses the same typed model (:func:`validate_gate_response`) so no
arbitrary schema string is ever interpreted and no JSON-Schema dependency is
introduced at runtime. ``GateResponseError`` lives here so this module stays
dependency-free (it imports only ``domain.enums``) and ``gates`` can depend on
it without a cycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from agents_orchestration.domain.enums import GateType


class GateResponseError(ValueError):
    """Base for Gate response validation failures (task 1.3 / 1.4)."""


BusinessField = Literal["clarification", "feedback", "resolution", "reason", "comment"]


@dataclass(frozen=True)
class OutcomeSpec:
    """One legal outcome for a Gate type and its business-field contract."""

    outcome: str
    required_field: BusinessField | None = None
    optional_fields: tuple[BusinessField, ...] = ()


# design Decision 1 — typed business-field contract per (Gate type, outcome).
GATE_RESPONSE_CONTRACT: dict[GateType, tuple[OutcomeSpec, ...]] = {
    GateType.GOAL_CLARIFICATION: (
        OutcomeSpec("clarified", required_field="clarification"),
        OutcomeSpec("cancelled", optional_fields=("reason",)),
    ),
    GateType.PLAN_APPROVAL: (
        OutcomeSpec("approved", optional_fields=("comment",)),
        OutcomeSpec("rejected", required_field="feedback"),
    ),
    GateType.CONFLICT_RESOLUTION: (
        OutcomeSpec("resolved", required_field="resolution"),
        OutcomeSpec("escalated", required_field="reason"),
    ),
    GateType.FINAL_REVIEW: (
        OutcomeSpec("approved", optional_fields=("comment",)),
        OutcomeSpec("changes", required_field="feedback"),
    ),
}


def outcomes_for(gate_type: GateType) -> tuple[str, ...]:
    """The legal outcome strings for a Gate type."""

    return tuple(spec.outcome for spec in GATE_RESPONSE_CONTRACT.get(gate_type, ()))


@dataclass(frozen=True)
class ValidatedGateResponse:
    """A Gate response payload after deterministic validation.

    Only the declared business field (if any) is populated; every other field
    is ``None`` so consumers read a stable, typed shape regardless of the Gate
    type that produced it.
    """

    outcome: str
    clarification: str | None = None
    feedback: str | None = None
    resolution: str | None = None
    reason: str | None = None
    comment: str | None = None


def validate_gate_response(gate_type: GateType, payload: object) -> ValidatedGateResponse:
    """Deterministically validate a Gate response payload (task 1.1 / 1.3).

    Rejects non-object payloads, missing/unknown/non-string outcomes, undeclared
    fields, wrong field types, and missing/blank required business fields,
    raising :class:`GateResponseError`. Nothing is mutated when this raises, so
    the caller can keep the Gate OPEN with the Run, dedup and event streams
    untouched.
    """

    if not isinstance(payload, dict):
        raise GateResponseError("response payload must be a JSON object")
    if "outcome" not in payload:
        raise GateResponseError("response payload missing 'outcome'")
    outcome = payload["outcome"]
    if not isinstance(outcome, str):
        raise GateResponseError("'outcome' must be a string")
    specs = GATE_RESPONSE_CONTRACT.get(gate_type, ())
    spec = next((s for s in specs if s.outcome == outcome), None)
    if spec is None:
        raise GateResponseError(f"unknown outcome '{outcome}' for {gate_type.value} gate")

    allowed: set[str] = {"outcome"}
    if spec.required_field is not None:
        allowed.add(spec.required_field)
    allowed.update(spec.optional_fields)
    extra = sorted(set(payload) - allowed)
    if extra:
        raise GateResponseError(f"unknown response fields: {extra}")

    values: dict[str, str] = {}
    if spec.required_field is not None:
        required = _text_field(payload, spec.required_field)
        if required is None:
            raise GateResponseError(
                f"outcome '{outcome}' requires non-empty '{spec.required_field}'"
            )
        values[spec.required_field] = required
    for optional in spec.optional_fields:
        present = _text_field(payload, optional, allow_blank=True)
        if optional in payload and present is None:
            raise GateResponseError(f"field '{optional}' must be a string")
        if present is not None:
            values[optional] = present
    return ValidatedGateResponse(outcome=outcome, **values)  # type: ignore[arg-type]


def _text_field(payload: dict, name: str, *, allow_blank: bool = False) -> str | None:
    """Return a stripped text field, or ``None`` if absent/invalid.

    Required fields reject blank values; optional fields accept any string
    (including empty) but still reject non-strings.
    """

    if name not in payload:
        return None
    value = payload[name]
    if not isinstance(value, str):
        return None
    if not allow_blank and not value.strip():
        return None
    return value


def canonical_response_schema(gate_type: GateType) -> str:
    """Render the canonical JSON Schema for a Gate type's allowed responses.

    Persisted on the Gate as ``allowed_response_schema`` for display/audit
    (task 1.2). Runtime validation uses :func:`validate_gate_response` against
    the same contract rather than interpreting this string.
    """

    specs = GATE_RESPONSE_CONTRACT.get(gate_type, ())
    one_of: list[dict[str, object]] = []
    for spec in specs:
        properties: dict[str, object] = {"outcome": {"type": "string", "enum": [spec.outcome]}}
        required = ["outcome"]
        if spec.required_field is not None:
            properties[spec.required_field] = {"type": "string", "minLength": 1}
            required.append(spec.required_field)
        for optional in spec.optional_fields:
            properties[optional] = {"type": "string"}
        one_of.append(
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
        )
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "oneOf": one_of,
    }
    return json.dumps(schema, sort_keys=True)


__all__ = [
    "GATE_RESPONSE_CONTRACT",
    "GateResponseError",
    "OutcomeSpec",
    "ValidatedGateResponse",
    "canonical_response_schema",
    "outcomes_for",
    "validate_gate_response",
]

"""Human Gate service: version-bound, single-use, at-most-once (tasks 9.1-9.8).

Gates are created atomically with the waiting state, a Domain Event, a semantic
Checkpoint and an Outbox record (9.3) before execution resources are released.
Responses are validated against actor/role/scope/schema/expiry/version/artifact
(9.4) and deduplicated by Request ID (9.5); a Gate is consumed at most once.
Expiry applies a configured action (9.7). Resume (9.6) happens by letting the
Tick open a fresh Attempt/Lease after the Gate is consumed — the old process is
never continued.
"""

from __future__ import annotations

from datetime import timedelta

from agents_orchestration.domain.enums import EffectType, GateType
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.lifecycle import CheckpointKind, Gate, GateContinuation
from agents_orchestration.domain.state_machine import assert_gate_consume, assert_gate_respond
from agents_orchestration.orchestration.gate_responses import (
    GateResponseError,
    ValidatedGateResponse,
    canonical_response_schema,
    validate_gate_response,
)
from agents_orchestration.runtime.core import CheckpointService


class GateNotOpenError(GateResponseError):
    pass


class GateExpiredError(GateResponseError):
    pass


class GateUnauthorizedError(GateResponseError):
    pass


class GateArtifactMismatchError(GateResponseError):
    pass


class DuplicateGateResponseError(GateResponseError):
    pass


class GateContinuationError(GateResponseError):
    """A Gate response could not be safely applied to the Run (task 3.3).

    The Gate has already been invalidated (CANCELED + ``GATE_INVALIDATED``) and
    the Run is unchanged; this stable error is surfaced to the caller.
    """

    pass


class GateService:
    def __init__(self, uow, clock, idgen, *, checkpoint: CheckpointService | None = None) -> None:
        self.uow = uow
        self.clock = clock
        self.idgen = idgen
        self._checkpoint = checkpoint

    def open(
        self,
        run: Run,
        gate_type: GateType,
        *,
        actor: str,
        role: str,
        scope: str,
        ttl_seconds: int = 3600,
        artifact_hash: str | None = None,
        task_id: str | None = None,
        continuation: GateContinuation | None = None,
        context: dict[str, object] | None = None,
    ) -> Gate:
        now = self.clock.now()
        gate = Gate(
            gate_id=self.idgen.new_id("gate"),
            run_id=run.run_id,
            gate_type=gate_type,
            actor=actor,
            role=role,
            scope=scope,
            state_version=run.state_version,
            plan_version=run.current_plan_version,
            task_id=task_id,
            artifact_hash=artifact_hash,
            allowed_response_schema=canonical_response_schema(gate_type),
            expires_at=now + timedelta(seconds=ttl_seconds),
            continuation=continuation,
            context=context or {},
        )
        self.uow.gates.save(gate)
        event = self._event(run, EffectType.GATE_OPENED, now, gate=gate)
        self.uow.events.append([event])
        self.uow.outbox.enqueue(run.run_id, [event])
        self._record_checkpoint(
            run, CheckpointKind.GATE, now, reason=f"gate_open:{gate_type.value}"
        )
        return gate

    def validate_response(
        self,
        gate: Gate,
        *,
        request_id: str,
        actor: str,
        role: str,
        payload: dict,
        expected_artifact_hash: str | None = None,
        allowed_actors: tuple[str, ...] | None = None,
    ) -> ValidatedGateResponse:
        """Run every pre-mutation check (task 1.3) and claim the Request ID.

        Lifecycle, expiry, typed payload, actor/role/artifact and at-most-once
        dedup are all enforced before any Gate/Run/event mutation. On success
        the dedup claim is held in the Unit of Work and the typed payload is
        returned; the caller commits RESPONDED/CONSUMED or CANCELED without a
        second claim.
        """

        now = self.clock.now()
        try:
            assert_gate_respond(gate.state)
        except ValueError as exc:
            raise GateNotOpenError(str(exc)) from None
        if gate.is_expired(now):
            raise GateExpiredError(f"gate {gate.gate_id} expired")
        validated = validate_gate_response(gate.gate_type, payload)
        if allowed_actors is not None and actor not in allowed_actors:
            raise GateUnauthorizedError(f"actor {actor} not permitted")
        if gate.role != role:
            raise GateUnauthorizedError(f"role {role} does not match {gate.role}")
        if expected_artifact_hash is not None and gate.artifact_hash != expected_artifact_hash:
            raise GateArtifactMismatchError("artifact hash mismatch")
        if not self.uow.dedup.try_claim(request_id, run_id=gate.run_id, kind="gate_response"):
            raise DuplicateGateResponseError(f"duplicate response {request_id}")
        return validated

    def respond(
        self,
        gate: Gate,
        *,
        request_id: str,
        actor: str,
        role: str,
        payload: dict,
        expected_artifact_hash: str | None = None,
        allowed_actors: tuple[str, ...] | None = None,
    ) -> Gate:
        self.validate_response(
            gate,
            request_id=request_id,
            actor=actor,
            role=role,
            payload=payload,
            expected_artifact_hash=expected_artifact_hash,
            allowed_actors=allowed_actors,
        )
        now = self.clock.now()
        responded = gate.respond(request_id=request_id, actor=actor, payload=payload, at=now)
        self.uow.gates.save(responded)
        self.uow.events.append([self._event_for(responded, EffectType.GATE_RESPONDED, now)])
        return responded

    def consume(self, gate: Gate) -> Gate:
        now = self.clock.now()
        try:
            assert_gate_consume(gate.state)
        except ValueError as exc:
            raise GateNotOpenError(str(exc)) from None
        consumed = gate.consume(now)
        self.uow.gates.save(consumed)
        self.uow.events.append([self._event_for(consumed, EffectType.GATE_CONSUMED, now)])
        return consumed

    def expire_open(self, run: Run, *, action: str = "fail") -> list[Gate]:
        now = self.clock.now()
        expired: list[Gate] = []
        for gate in self.uow.gates.open_for_run(run.run_id):
            if not gate.is_expired(now):
                continue
            dead = gate.expire()
            self.uow.gates.save(dead)
            self.uow.events.append(
                [self._event_for(dead, EffectType.GATE_EXPIRED, now, action=action)]
            )
            expired.append(dead)
        return expired

    def _event(self, run: Run, effect: EffectType, at, *, gate: Gate) -> DomainEvent:
        return DomainEvent(
            event_id=self.idgen.new_id("evt"),
            run_id=run.run_id,
            effect=effect,
            state_version=run.state_version,
            occurred_at=at,
            gate_id=gate.gate_id,
            plan_version=run.current_plan_version,
            payload={"gate_type": gate.gate_type.value, "state_version_bound": gate.state_version},
        )

    def _event_for(
        self, gate: Gate, effect: EffectType, at, *, action: str | None = None
    ) -> DomainEvent:
        payload: dict = {"gate_type": gate.gate_type.value}
        if action:
            payload["expiry_action"] = action
        return DomainEvent(
            event_id=self.idgen.new_id("evt"),
            run_id=gate.run_id,
            effect=effect,
            state_version=gate.state_version,
            occurred_at=at,
            gate_id=gate.gate_id,
            plan_version=gate.plan_version,
            payload=payload,
        )

    def _record_checkpoint(self, run: Run, kind: CheckpointKind, at, *, reason: str) -> None:
        if self._checkpoint is None:
            self._checkpoint = CheckpointService(self.uow, self.clock, self.idgen)
        self._checkpoint.record(
            run_id=run.run_id,
            kind=kind,
            state_version=run.state_version,
            plan_version=run.current_plan_version,
            reason=reason,
        )


__all__ = [
    "DuplicateGateResponseError",
    "GateArtifactMismatchError",
    "GateContinuationError",
    "GateExpiredError",
    "GateNotOpenError",
    "GateResponseError",
    "GateService",
    "GateUnauthorizedError",
]

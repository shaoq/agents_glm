"""Integration tests for Human Gates and controlled resume (Section 9)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import EffectType, GateState, GateType
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.gates import (
    DuplicateGateResponseError,
    GateArtifactMismatchError,
    GateExpiredError,
    GateNotOpenError,
    GateService,
    GateUnauthorizedError,
)
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome
from tests.integration.test_runtime import _seed


def _open(backend, clock, *, gate_type=GateType.PLAN_APPROVAL, artifact_hash=None, ttl=3600):
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        gate = GateService(uow, clock, backend.idgen).open(
            run,
            gate_type,
            actor="approver",
            role="approver",
            scope="plan",
            allowed_response_schema="{}",
            ttl_seconds=ttl,
            artifact_hash=artifact_hash,
        )
        uow.commit()
    return gate


# --- 9.1 / 9.2 / 9.3 open binds version + persists atomically ---------------


@pytest.mark.integration
def test_open_gate_binds_version_and_persists_event_checkpoint_outbox(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    gate = _open(backend, fake_clock, artifact_hash="sha256:abc")
    assert gate.state_version == 1
    assert gate.plan_version == 1
    assert gate.artifact_hash == "sha256:abc"
    with backend.unit_of_work() as uow:
        effects = [e.effect for e in uow.events.stream("r1")]
        assert EffectType.GATE_OPENED in effects
        assert uow.checkpoints.latest("r1") is not None
        assert len(uow.outbox.pending()) >= 1


# --- 9.4 response validation ------------------------------------------------


@pytest.mark.integration
def test_respond_rejects_unauthorized_actor_and_role(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    gate = _open(backend, fake_clock)
    with backend.unit_of_work() as uow:
        svc = GateService(uow, fake_clock, backend.idgen)
        with pytest.raises(GateUnauthorizedError):
            svc.respond(
                gate,
                request_id="rq1",
                actor="eve",
                role="approver",
                payload={},
                allowed_actors=("approver",),
            )
        with pytest.raises(GateUnauthorizedError):
            svc.respond(gate, request_id="rq1", actor="approver", role="intruder", payload={})
        uow.rollback()


@pytest.mark.integration
def test_respond_rejects_artifact_mismatch(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    gate = _open(backend, fake_clock, artifact_hash="sha256:abc")
    with backend.unit_of_work() as uow:
        svc = GateService(uow, fake_clock, backend.idgen)
        with pytest.raises(GateArtifactMismatchError):
            svc.respond(
                gate,
                request_id="rq1",
                actor="approver",
                role="approver",
                payload={},
                expected_artifact_hash="sha256:different",
            )
        uow.rollback()


# --- 9.5 at-most-once consumption -------------------------------------------


@pytest.mark.integration
def test_duplicate_response_is_rejected_and_consume_is_single_use(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    gate = _open(backend, fake_clock)
    # Dedup: two responds with the same request_id against the OPEN gate in one
    # transaction — the second must be rejected as a duplicate (9.5).
    with backend.unit_of_work() as uow:
        svc = GateService(uow, fake_clock, backend.idgen)
        svc.respond(gate, request_id="rq1", actor="approver", role="approver", payload={})
        with pytest.raises(DuplicateGateResponseError):
            svc.respond(gate, request_id="rq1", actor="approver", role="approver", payload={})
        uow.rollback()
    # Consume once succeeds; a second consume is rejected (single-use, 9.5).
    with backend.unit_of_work() as uow:
        svc = GateService(uow, fake_clock, backend.idgen)
        responded = svc.respond(
            gate, request_id="rq2", actor="approver", role="approver", payload={}
        )
        consumed = svc.consume(responded)
        uow.commit()
    with backend.unit_of_work() as uow:
        with pytest.raises(GateNotOpenError):
            GateService(uow, fake_clock, backend.idgen).consume(consumed)
        uow.rollback()
    with backend.unit_of_work() as uow:
        assert uow.gates.get(gate.gate_id).state is GateState.CONSUMED


# --- 9.7 expiry -------------------------------------------------------------


@pytest.mark.integration
def test_expire_open_gates_marks_expired(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    _open(backend, fake_clock, ttl=1)
    fake_clock.advance(60)
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        expired = GateService(uow, fake_clock, backend.idgen).expire_open(run, action="fail")
        uow.commit()
    assert len(expired) == 1
    with backend.unit_of_work() as uow:
        gate = uow.gates.get(expired[0].gate_id)
        assert gate.state is GateState.EXPIRED
        assert any(e.effect is EffectType.GATE_EXPIRED for e in uow.events.stream("r1"))


@pytest.mark.integration
def test_respond_rejects_expired_gate(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    gate = _open(backend, fake_clock, ttl=1)
    fake_clock.advance(60)
    with backend.unit_of_work() as uow:
        with pytest.raises(GateExpiredError):
            GateService(uow, fake_clock, backend.idgen).respond(
                gate,
                request_id="rq1",
                actor="approver",
                role="approver",
                payload={},
            )
        uow.rollback()


# --- 9.6 / tick integration: block on open gate, resume after consume -------


@pytest.mark.integration
async def test_tick_blocks_on_open_gate_and_resumes_after_consume(backend, fake_clock) -> None:
    _seed(backend, fake_clock, run_id="r1", task_ids=("t1",))
    tick = RuntimeTick(backend, executor=_succeed_holder(backend), limits=SystemLimits())

    gate = _open(backend, fake_clock)
    blocked = await tick.tick("r1")
    assert blocked.blocked

    # Respond + consume, then the Tick resumes by dispatching a fresh Attempt.
    with backend.unit_of_work() as uow:
        svc = GateService(uow, fake_clock, backend.idgen)
        responded = svc.respond(
            gate, request_id="rq1", actor="approver", role="approver", payload={}
        )
        svc.consume(responded)
        uow.commit()

    resumed = await tick.tick("r1")
    assert resumed.dispatched == 1


def _succeed_holder(backend):
    class _Exec:
        async def execute(self, task, attempt, run):
            return TaskExecutionOutcome(succeeded=True)

    return _Exec()

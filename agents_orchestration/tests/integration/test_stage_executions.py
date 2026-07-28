"""Integration tests for StageExecution persistence (Ch.3 tasks 3.1-3.9)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef
from agents_orchestration.domain.coordination import (
    InputFingerprint,
    PhaseId,
    StageExecution,
    StageStatus,
    stage_logical_key,
)
from agents_orchestration.domain.enums import FailureCode, RunState
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.runtime.persistence import schema as schema_mod
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from agents_orchestration.runtime.ports import ConcurrencyError

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _fingerprint(state: int = 1, plan: int | None = None) -> InputFingerprint:
    return InputFingerprint(state_version=state, plan_version=plan)


def _stage(
    run: str = "run-1",
    phase: PhaseId = PhaseId.GOAL,
    fp: InputFingerprint | None = None,
    key: str = "k1",
    se_id: str = "se-1",
) -> StageExecution:
    return StageExecution(
        stage_execution_id=se_id,
        run_id=run,
        phase=phase,
        logical_stage_key=stage_logical_key(phase),
        fingerprint=fp or _fingerprint(),
        idempotency_key=key,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_run(run_id: str = "run-old") -> Run:
    return Run(
        run_id=run_id,
        raw_goal="g",
        state=RunState.NORMALIZING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=NOW,
        updated_at=NOW,
    )


# --- Task 3.1: fresh migration ---------------------------------------------


@pytest.mark.integration
def test_fresh_database_has_stage_executions_schema(tmp_path) -> None:
    backend = SqliteBackend(tmp_path / "rt.sqlite", tmp_path / "arts")
    assert schema_mod.schema_version(backend.conn) == 2
    cols = {r["name"] for r in backend.conn.execute("PRAGMA table_info(stage_executions)")}
    assert {
        "stage_execution_id",
        "run_id",
        "logical_stage_key",
        "fingerprint_hex",
        "status",
        "idempotency_key",
    } <= cols
    # partial unique index exists
    idx = backend.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='ux_stage_exec_accepted'"
    ).fetchone()
    assert idx is not None


# --- Task 3.2: upgrade migration preserves Run history ---------------------


@pytest.mark.integration
def test_upgrade_from_v1_preserves_run_history(tmp_path) -> None:
    db = tmp_path / "rt.sqlite"
    # Simulate a v1 database: only the runs table, user_version=1, with a Run row.
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, state TEXT, state_version INTEGER, "
        "termination TEXT, data TEXT)"
    )
    conn.execute("PRAGMA user_version = 1")
    run = _make_run()
    conn.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?)",
        (run.run_id, run.state.value, run.state_version, None, run.model_dump_json()),
    )
    conn.commit()
    conn.close()

    # Opening with current code runs additive initialize -> schema_version 2.
    backend = SqliteBackend(db, tmp_path / "arts")
    assert schema_mod.schema_version(backend.conn) == 2
    with backend.unit_of_work() as uow:
        assert uow.runs.get("run-old").run_id == "run-old"  # history preserved
        # stage_executions table now usable
        assert uow.stages.accepted_for("run-old", "goal", "deadbeef") is None
        uow.commit()


# --- Task 3.4: repository create / lookup ----------------------------------


@pytest.mark.integration
def test_save_and_get_stage(backend) -> None:
    with backend.unit_of_work() as uow:
        uow.stages.save(_stage())
        got = uow.stages.get("se-1")
        assert got is not None
        assert got.status is StageStatus.PREPARED
        assert got.fingerprint.state_version == 1
        uow.commit()


# --- Task 3.5: one accepted per run + key + fingerprint --------------------


@pytest.mark.integration
def test_second_accept_reuses_first_accepted(backend) -> None:
    with backend.unit_of_work() as uow:
        fp = _fingerprint(state=2)
        a = _stage(se_id="se-a", fp=fp, key="ka")
        uow.stages.save(a)
        uow.stages.accept("se-a", accepted=a.transition(StageStatus.ACCEPTED, at=NOW))
        # A second record for the same fingerprint must reuse the first accepted.
        b = _stage(se_id="se-b", fp=fp, key="kb")
        uow.stages.save(b)
        reused = uow.stages.accept("se-b", accepted=b.transition(StageStatus.ACCEPTED, at=NOW))
        assert reused.stage_execution_id == "se-a"
        # Still exactly one accepted row.
        rows = uow.stages.for_logical_stage("run-1", stage_logical_key(PhaseId.GOAL))
        assert len([r for r in rows if r.status is StageStatus.ACCEPTED]) == 1
        uow.commit()


# --- Task 3.6: idempotent prepare + accepted reuse -------------------------


@pytest.mark.integration
def test_prepare_is_idempotent_by_idempotency_key(backend) -> None:
    with backend.unit_of_work() as uow:
        out1 = uow.stages.prepare(_stage(se_id="se-1", key="idem-1"))
        out2 = uow.stages.prepare(_stage(se_id="se-2", key="idem-1"))
        assert out1.stage_execution_id == "se-1"
        assert out2.stage_execution_id == "se-1"  # replayed, not duplicated
        uow.commit()


@pytest.mark.integration
def test_prepare_reuses_accepted_result(backend) -> None:
    with backend.unit_of_work() as uow:
        fp = _fingerprint(state=3)
        first = _stage(se_id="se-1", fp=fp, key="k1")
        uow.stages.save(first)
        uow.stages.accept("se-1", accepted=first.transition(StageStatus.ACCEPTED, at=NOW))
        replay = _stage(se_id="se-2", fp=fp, key="k2")
        out = uow.stages.prepare(replay)
        assert out.stage_execution_id == "se-1"
        assert out.status is StageStatus.ACCEPTED
        uow.commit()


# --- Task 3.7: compare-and-set concurrent accept ---------------------------


@pytest.mark.integration
def test_accept_cas_rejects_non_prepared_status(backend) -> None:
    with backend.unit_of_work() as uow:
        stage = _stage()
        uow.stages.save(stage)
        uow.stages.transition_status("se-1", StageStatus.REJECTED, at=NOW)
        with pytest.raises(ConcurrencyError):
            uow.stages.accept("se-1", accepted=stage.transition(StageStatus.ACCEPTED, at=NOW))
        uow.commit()


# --- Task 3.8: UnitOfWork integration --------------------------------------


@pytest.mark.integration
def test_stage_repository_participates_in_unit_of_work(backend) -> None:
    with backend.unit_of_work() as uow:
        uow.stages.save(_stage())
        # exit without commit -> rolled back
    with backend.unit_of_work() as uow:
        assert uow.stages.get("se-1") is None
        uow.commit()


# --- Task 3.9: serialization round-trip ------------------------------------


@pytest.mark.integration
def test_stage_serialization_round_trip(backend) -> None:
    fp = InputFingerprint(
        state_version=2,
        plan_version=3,
        contract_version=1,
        input_artifact_hashes=("sha256:h1",),
    )
    stage = StageExecution(
        stage_execution_id="se-rt",
        run_id="run-1",
        phase=PhaseId.RESEARCH,
        logical_stage_key=stage_logical_key(PhaseId.RESEARCH, context="plan_v2"),
        fingerprint=fp,
        status=StageStatus.ACCEPTED,
        output_artifact_refs=(
            ArtifactRef(
                artifact_id="a1",
                content_hash="sha256:h1",
                path="/p",
                size_bytes=10,
                kind=ArtifactKind.EVIDENCE,
            ),
        ),
        output_entity_ids=("e1",),
        failure_code=FailureCode.TIMEOUT,
        idempotency_key="krt",
        created_at=NOW,
        updated_at=NOW,
    )
    with backend.unit_of_work() as uow:
        uow.stages.save(stage)
        got = uow.stages.get("se-rt")
        assert got == stage
        assert got.fingerprint.input_artifact_hashes == ("sha256:h1",)
        assert got.output_artifact_refs[0].artifact_id == "a1"
        assert got.output_entity_ids == ("e1",)
        assert got.failure_code is FailureCode.TIMEOUT
        uow.commit()

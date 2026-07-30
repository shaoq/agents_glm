"""Integration tests for AnalysisArtifact persistence + authoritative handoff (task 3.4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.domain.artifact import hash_content
from agents_orchestration.domain.coordination import (
    InputFingerprint,
    PhaseId,
    StageExecution,
    StageStatus,
)
from agents_orchestration.domain.enums import RunState
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.analysis_artifact import (
    AnalysisArtifactRef,
    SqliteAnalysisArtifactStore,
    accepted_analysis_ref,
)
from agents_orchestration.orchestration.coordinator import RunCoordinator
from agents_orchestration.orchestration.phases import AnalysisPhaseHandler, WritingPhaseHandler
from agents_orchestration.orchestration.report import AnalysisArtifact, ReportContent
from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore
from agents_orchestration.runtime.ports import OrphanArtifactError

_NOW = datetime(2026, 7, 27, tzinfo=UTC)


def _store(backend) -> SqliteAnalysisArtifactStore:
    return SqliteAnalysisArtifactStore(SqliteArtifactStore(backend.conn, backend.artifact_dir))


def _analysis(run_id: str = "r1") -> AnalysisArtifact:
    return AnalysisArtifact(
        run_id=run_id,
        conclusions=("c1", "c2"),
        cited_evidence_ids=("e1",),
        open_questions=("q1",),
    )


def _save_accepted_stage(
    backend,
    *,
    run_id: str,
    plan_version: int,
    state_version: int,
    ref: AnalysisArtifactRef | None,
) -> None:
    stage = StageExecution(
        stage_execution_id=f"stage-{run_id}-{plan_version}-{state_version}",
        run_id=run_id,
        phase=PhaseId.ANALYZE,
        logical_stage_key="analyze",
        fingerprint=InputFingerprint(state_version=state_version, plan_version=plan_version),
        status=StageStatus.ACCEPTED,
        output_artifact_refs=(ref.as_artifact_ref(),) if ref else (),
        idempotency_key=f"{run_id}|analyze|{plan_version}-{state_version}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    with backend.unit_of_work() as uow:
        uow.stages.save(stage)
        uow.commit()


# --- materialize / load round-trip ----------------------------------------


@pytest.mark.integration
async def test_materialize_load_roundtrip_preserves_analysis(backend):
    store = _store(backend)
    ref = await store.materialize(
        run_id="r1",
        plan_version=1,
        analysis=_analysis(),
        source_evidence_hash="sha256:ev",
    )
    assert ref.artifact_id.startswith("analysis_")
    assert ref.content_hash.startswith("sha256:")
    assert ref.plan_version == 1
    assert ref.source_evidence_hash == "sha256:ev"

    loaded = await store.load(ref)
    assert loaded == _analysis()


@pytest.mark.integration
async def test_load_rejects_hash_mismatch(backend):
    store = _store(backend)
    ref = await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev"
    )
    tampered = AnalysisArtifactRef(
        artifact_id=ref.artifact_id,
        content_hash="sha256:deadbeef",
        run_id=ref.run_id,
        plan_version=ref.plan_version,
        source_evidence_hash=ref.source_evidence_hash,
        path=ref.path,
        size_bytes=ref.size_bytes,
    )
    with pytest.raises(OrphanArtifactError):
        await store.load(tampered)


@pytest.mark.integration
async def test_materialize_is_deterministic_for_same_content(backend):
    store = _store(backend)
    a = await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev"
    )
    b = await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev"
    )
    assert a.artifact_id == b.artifact_id
    assert a.content_hash == b.content_hash


# --- authority: ACCEPTED stage is the sole selector -----------------------


@pytest.mark.integration
async def test_unreferenced_blob_is_invisible(backend):
    """A materialized blob with no ACCEPTED stage is an orphan (task 3.4)."""

    store = _store(backend)
    await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev"
    )
    # No ACCEPTED ANALYZE stage references it.
    assert accepted_analysis_ref(_stages(backend), "r1", 1) is None


@pytest.mark.integration
async def test_accepted_ref_loads_for_current_plan(backend):
    store = _store(backend)
    ref = await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev"
    )
    _save_accepted_stage(backend, run_id="r1", plan_version=1, state_version=3, ref=ref)

    found = accepted_analysis_ref(_stages(backend), "r1", 1)
    assert found is not None
    assert found.artifact_id == ref.artifact_id
    assert found.content_hash == ref.content_hash
    loaded = await store.load(found)
    assert loaded == _analysis()


@pytest.mark.integration
async def test_stale_plan_candidate_is_isolated(backend):
    """A candidate accepted for an older plan is not loadable for the current plan."""

    store = _store(backend)
    old_ref = await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev"
    )
    _save_accepted_stage(backend, run_id="r1", plan_version=1, state_version=3, ref=old_ref)
    # Current plan is now v2 after a replan; the v1 accepted analysis is stale.
    assert accepted_analysis_ref(_stages(backend), "r1", 2) is None


@pytest.mark.integration
async def test_latest_accepted_for_current_plan_wins(backend):
    store = _store(backend)
    v1_ref = await store.materialize(
        run_id="r1", plan_version=1, analysis=_analysis(), source_evidence_hash="sha256:ev1"
    )
    _save_accepted_stage(backend, run_id="r1", plan_version=1, state_version=3, ref=v1_ref)
    v2_analysis = AnalysisArtifact(run_id="r1", conclusions=("new",))
    v2_ref = await store.materialize(
        run_id="r1", plan_version=2, analysis=v2_analysis, source_evidence_hash="sha256:ev2"
    )
    _save_accepted_stage(backend, run_id="r1", plan_version=2, state_version=9, ref=v2_ref)

    found = accepted_analysis_ref(_stages(backend), "r1", 2)
    assert found is not None
    assert found.artifact_id == v2_ref.artifact_id
    assert found.artifact_id != v1_ref.artifact_id


def _stages(backend):
    """A stages-repo-like adapter exposing for_logical_stage over the connection."""

    from agents_orchestration.runtime.persistence.repositories import (
        SqliteStageExecutionRepository,
    )

    return SqliteStageExecutionRepository(backend.conn)


# --- end-to-end identity: reviewer/accept/writer share one artifact (3.5) --


@pytest.mark.integration
async def test_writer_consumes_same_artifact_as_accepted_stage(backend) -> None:
    """The artifact the writer reads is the same entity/content the ANALYZE stage
    accepted — never a re-derived Analysis (task 3.5)."""

    run = Run(
        run_id=backend.idgen.new_id("run"),
        raw_goal="g",
        state=RunState.ANALYZING,
        policy=RunPolicy.from_limits(SystemLimits()),
        current_plan_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()

    store = _store(backend)

    async def analyst(run_id, evidence):
        return AnalysisArtifact(run_id=run_id, conclusions=("c1", "c2"), cited_evidence_ids=("e1",))

    async def evidence(run_id):
        return EvidenceSet.join(run_id=run_id, task_id="research", evidences=(), required=False)

    analyze_handler = AnalysisPhaseHandler(
        analyst, evidence, store, clock=backend.clock, idgen=backend.idgen
    )
    report = await RunCoordinator(backend, {PhaseId.ANALYZE: analyze_handler}).advance(run.run_id)
    assert report.to_state is RunState.WRITING

    captured: dict[str, str] = {}

    async def writer(run_id, analysis):
        captured["hash"] = hash_content(analysis.model_dump_json().encode("utf-8"))
        return ReportContent(run_id=run_id, title="T", objective="O")

    async def analysis_provider(run_id):
        with backend.unit_of_work() as uow:
            current = uow.runs.get(run_id)
            ref = accepted_analysis_ref(uow.stages, run_id, current.current_plan_version)
            uow.commit()
        return await store.load(ref)

    write_handler = WritingPhaseHandler(
        writer, analysis_provider, clock=backend.clock, idgen=backend.idgen
    )
    report = await RunCoordinator(backend, {PhaseId.WRITE: write_handler}).advance(run.run_id)
    assert report.to_state is RunState.REVIEWING

    with backend.unit_of_work() as uow:
        ref = accepted_analysis_ref(uow.stages, run.run_id, 1)
        uow.commit()
    assert ref is not None
    # Same entity id + content hash flowed from ANALYZE accept into the writer.
    assert ref.content_hash == captured["hash"]
    assert ref.artifact_id.startswith("analysis_")

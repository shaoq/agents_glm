"""Integration tests for SQLite persistence (tasks 3.5 / 3.7 / 3.8 / 3.9)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.artifact import ArtifactKind
from agents_orchestration.domain.enums import EffectType, RunState, WorkerRole
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.lifecycle import Checkpoint, CheckpointKind, Lease, LeaseState
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.runtime.ports import (
    ConcurrencyError,
    OrphanArtifactError,
    StaleVersionError,
)


def _new_run(run_id: str, clock) -> Run:
    now = clock.now()
    return Run(
        run_id=run_id,
        raw_goal="goal",
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=now,
        updated_at=now,
    )


# --- 3.5 Run compare-and-set -------------------------------------------------


@pytest.mark.integration
def test_run_save_inserts_then_cas_updates(backend, fake_clock) -> None:
    run = _new_run("r1", fake_clock)
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()

    with backend.unit_of_work() as uow:
        fetched = uow.runs.get("r1")
        assert fetched is not None and fetched.state_version == 1
        moved = fetched.transition(RunState.NORMALIZING, fake_clock.now())
        uow.runs.save(moved, expected_version=fetched.state_version)
        uow.commit()

    with backend.unit_of_work() as uow:
        assert uow.runs.get("r1").state_version == 2


@pytest.mark.integration
def test_run_cas_rejects_stale_version(backend, fake_clock) -> None:
    run = _new_run("r2", fake_clock)
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()
    with backend.unit_of_work() as uow:
        fetched = uow.runs.get("r2")
        moved = fetched.transition(RunState.NORMALIZING, fake_clock.now())
        uow.runs.save(moved, expected_version=fetched.state_version)
        uow.commit()
    # Now stored version is 2; writing with stale expected_version=1 must fail.
    with backend.unit_of_work() as uow:
        stale = _new_run("r2", fake_clock)  # state_version == 1
        with pytest.raises(StaleVersionError):
            uow.runs.save(stale, expected_version=1)
        uow.rollback()


# --- 3.5 Lease epoch fencing -------------------------------------------------


@pytest.mark.integration
def test_lease_claim_and_fence(backend, fake_clock) -> None:
    now = fake_clock.now()
    lease = Lease(
        task_id="t1",
        attempt_id="a1",
        run_id="r1",
        epoch=1,
        claimed_at=now,
        expires_at=now,
    )
    with backend.unit_of_work() as uow:
        uow.leases.save(lease)  # new claim, epoch 1
        uow.commit()

    with backend.unit_of_work() as uow:
        # Recovery/reclaim advanced the task to epoch 2.
        reclaimed = Lease(
            task_id="t1",
            attempt_id="a2",
            run_id="r1",
            epoch=2,
            claimed_at=fake_clock.now(),
            expires_at=fake_clock.now(),
        )
        uow.leases.save(reclaimed)
        uow.commit()

    # The stale epoch-1 holder must be fenced when it tries to commit.
    with backend.unit_of_work() as uow:
        with pytest.raises(ConcurrencyError):
            uow.leases.save(lease.renew(fake_clock.now(), fake_clock.now()), expected_epoch=1)
        uow.rollback()


@pytest.mark.integration
def test_lease_renew_within_epoch_succeeds(backend, fake_clock) -> None:
    now = fake_clock.now()
    lease = Lease(
        task_id="t2",
        attempt_id="a2",
        run_id="r1",
        epoch=1,
        claimed_at=now,
        expires_at=now,
    )
    with backend.unit_of_work() as uow:
        uow.leases.save(lease)
        renewed = lease.renew(fake_clock.now(), fake_clock.now())
        uow.leases.save(renewed, expected_epoch=1)
        uow.commit()

    with backend.unit_of_work() as uow:
        assert uow.leases.get("t2").state is LeaseState.RENEWED


# --- 3.9 Atomic transaction rollback ----------------------------------------


@pytest.mark.integration
def test_rollback_hides_state_event_checkpoint_and_outbox(backend, fake_clock) -> None:
    run = _new_run("r3", fake_clock)
    # First persist the run so we have something to observe rollback against.
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.commit()

    event = DomainEvent(
        event_id="e1",
        run_id="r3",
        effect=EffectType.RUN_PAUSED,
        state_version=2,
        occurred_at=fake_clock.now(),
    )
    checkpoint = Checkpoint(
        checkpoint_id="c1",
        run_id="r3",
        kind=CheckpointKind.RETRY,
        state_version=2,
        reason="rollback probe",
        created_at=fake_clock.now(),
    )

    with backend.unit_of_work() as uow:
        moved = run.transition(RunState.PAUSED, fake_clock.now())
        uow.runs.save(moved, expected_version=1)
        uow.events.append([event])
        uow.checkpoints.save(checkpoint)
        uow.outbox.enqueue("r3", [event])
        uow.rollback()  # discard everything

    with backend.unit_of_work() as uow:
        assert uow.runs.get("r3").state is RunState.CREATED
        assert list(uow.events.stream("r3")) == []
        assert uow.checkpoints.latest("r3") is None
        assert uow.outbox.pending() == []


@pytest.mark.integration
def test_commit_makes_state_event_checkpoint_outbox_visible_together(backend, fake_clock) -> None:
    run = _new_run("r4", fake_clock)
    event = DomainEvent(
        event_id="e2",
        run_id="r4",
        effect=EffectType.RUN_PAUSED,
        state_version=2,
        occurred_at=fake_clock.now(),
    )
    checkpoint = Checkpoint(
        checkpoint_id="c2",
        run_id="r4",
        kind=CheckpointKind.PLAN,
        state_version=2,
        reason="commit probe",
        created_at=fake_clock.now(),
    )

    with backend.unit_of_work() as uow:
        moved = run.transition(RunState.PAUSED, fake_clock.now())
        uow.runs.save(moved, expected_version=1)
        uow.events.append([event])
        uow.checkpoints.save(checkpoint)
        uow.outbox.enqueue("r4", [event])
        uow.commit()

    with backend.unit_of_work() as uow:
        assert uow.runs.get("r4").state is RunState.PAUSED
        assert len(list(uow.events.stream("r4"))) == 1
        assert uow.checkpoints.latest("r4") is not None
        assert len(uow.outbox.pending()) == 1


# --- 3.7 Content-addressed Artifact Store -----------------------------------


@pytest.mark.integration
def test_artifact_store_content_addressed_dedup_and_verify(backend) -> None:
    content = b"# Report\n\nhello"
    with backend.unit_of_work() as uow:
        ref = uow.artifacts.write(content, kind=ArtifactKind.REPORT_MARKDOWN)
        uow.artifacts.record_metadata(ref)
        again = uow.artifacts.write(content, kind=ArtifactKind.REPORT_MARKDOWN)
        assert again.content_hash == ref.content_hash
        uow.commit()

    with backend.unit_of_work() as uow:
        found = uow.artifacts.find(ref.content_hash)
        assert found is not None and found.verify(content)
        assert uow.artifacts.read(found) == content
        assert not found.verify(b"tampered")


# --- 3.8 Orphan detection ---------------------------------------------------


@pytest.mark.integration
def test_orphan_detection_and_cleanup(backend) -> None:
    with backend.unit_of_work() as uow:
        ref = uow.artifacts.write(b"committed", kind=ArtifactKind.EVIDENCE)
        uow.artifacts.record_metadata(ref)
        # Write a second artifact whose metadata is never recorded (simulating a
        # file written before a failed transaction).
        orphan_ref = uow.artifacts.write(b"orphaned", kind=ArtifactKind.EVIDENCE)
        uow.commit()

    with backend.unit_of_work() as uow:
        orphans = uow.artifacts.list_orphans()
        orphan_paths = [p.name for p in orphans]
        assert orphan_ref.path in orphan_paths
        assert ref.path not in orphan_paths
        # Referenced artifacts cannot be deleted.
        referenced_path = backend.artifact_dir / ref.path
        with pytest.raises(OrphanArtifactError):
            uow.artifacts.delete_orphan(referenced_path)
        # Orphans can be cleaned up.
        uow.artifacts.delete_orphan(backend.artifact_dir / orphan_ref.path)
        uow.commit()

    with backend.unit_of_work() as uow:
        assert uow.artifacts.list_orphans() == []


# --- task materialize + repository round trip -------------------------------


@pytest.mark.integration
def test_task_materialize_and_query(backend, fake_clock) -> None:
    now = fake_clock.now()
    task = Task(
        task_id="t9",
        run_id="r9",
        plan_version=1,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        created_at=now,
        updated_at=now,
    )
    with backend.unit_of_work() as uow:
        uow.tasks.materialize([task])
        uow.commit()
    with backend.unit_of_work() as uow:
        fetched = uow.tasks.get("t9")
        assert fetched is not None and fetched.worker_role is WorkerRole.EVIDENCE_RESEARCHER
        assert [t.task_id for t in uow.tasks.by_run("r9")] == ["t9"]


@pytest.mark.integration
def test_request_dedup_at_most_once(backend) -> None:
    with backend.unit_of_work() as uow:
        assert uow.dedup.try_claim("req-1", run_id="r1", kind="start_run") is True
        assert uow.dedup.try_claim("req-1", run_id="r1", kind="start_run") is False
        uow.dedup.remember("req-1", {"ok": True})
        uow.commit()
    with backend.unit_of_work() as uow:
        assert uow.dedup.recall("req-1") == {"ok": True}
        assert uow.dedup.recall("missing") is None

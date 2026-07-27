"""Unit tests for domain model behavior (tasks 2.1-2.4, 2.9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef, hash_content
from agents_orchestration.domain.enums import (
    AttemptAcceptance,
    AttemptState,
    BranchRole,
    FailureCode,
    GateState,
    GateType,
    RunState,
    Sufficiency,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.evidence import (
    Evidence,
    EvidenceSet,
    SourceIdentity,
    SourceKind,
)
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.goal import (
    CompletionContract,
    CompletionCriterion,
    CriterionKind,
    GoalSpec,
)
from agents_orchestration.domain.lifecycle import Gate, Lease, LeaseState
from agents_orchestration.domain.plan import Dependency, DependencyKind, PlanGraph, TaskSpec
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def _policy() -> RunPolicy:
    return RunPolicy.from_limits(SystemLimits())


# --- Run / Task construction + immutability ---------------------------------


@pytest.mark.unit
def test_run_default_state_is_created_and_immutable() -> None:
    run = Run(run_id="r1", raw_goal="g", policy=_policy(), created_at=NOW, updated_at=NOW)
    assert run.state is RunState.CREATED
    assert run.state_version == 1
    with pytest.raises(ValidationError):
        run.state = RunState.PLANNING  # type: ignore[misc]


@pytest.mark.unit
def test_run_transition_bumps_state_version() -> None:
    run = Run(run_id="r1", raw_goal="g", policy=_policy(), created_at=NOW, updated_at=NOW)
    moved = run.transition(RunState.NORMALIZING, NOW)
    assert moved.state is RunState.NORMALIZING
    assert moved.state_version == 2
    assert run.state_version == 1  # original unchanged (immutable)


@pytest.mark.unit
def test_run_terminate_maps_reason_to_terminal_state() -> None:
    run = Run(run_id="r1", raw_goal="g", policy=_policy(), created_at=NOW, updated_at=NOW)
    assert run.terminate(TerminationReason.COMPLETED, NOW).state is RunState.SUCCEEDED
    assert run.terminate(TerminationReason.CANCELED, NOW).state is RunState.CANCELED
    assert run.terminate(TerminationReason.DEADLINE_EXCEEDED, NOW).state is RunState.FAILED


@pytest.mark.unit
def test_task_defaults_and_terminal_flags() -> None:
    task = Task(
        task_id="t1",
        run_id="r1",
        plan_version=1,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        created_at=NOW,
        updated_at=NOW,
    )
    assert task.state is TaskState.PENDING
    assert not task.is_terminal
    done = task.transition(TaskState.SUCCEEDED, NOW, accepted_attempt_id="a1")
    assert done.is_terminal and done.state is TaskState.SUCCEEDED


# --- Budget -----------------------------------------------------------------


@pytest.mark.unit
def test_budget_consume_is_immutable_and_tracks_usage() -> None:
    budget = Budget(max_tokens=100, max_cost_usd=Decimal("1.00"))
    spent = budget.consume(tokens=30, cost_usd="0.40")
    assert budget.tokens_used == 0
    assert spent.tokens_used == 30
    assert spent.cost_usd_used == Decimal("0.40")


@pytest.mark.unit
def test_budget_exhaustion_flags() -> None:
    deadline = NOW + timedelta(seconds=60)
    budget = Budget(deadline_at=deadline, max_tokens=10)
    assert not budget.exhausted(now=NOW)
    assert budget.consume(tokens=10).exhausted(now=NOW)
    assert budget.exhausted(now=deadline)


@pytest.mark.unit
def test_budget_rejects_overrun_at_construction() -> None:
    with pytest.raises(ValueError):
        Budget(max_tokens=10, tokens_used=20)


# --- PlanGraph invariants ---------------------------------------------------


def _spec(tid: str, deps: tuple[str, ...] = (), depth: int = 1) -> TaskSpec:
    return TaskSpec(
        task_id=tid,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        description=tid,
        depends_on=deps,
        depth=depth,
    )


@pytest.mark.unit
def test_plangraph_detects_cycle() -> None:
    graph = PlanGraph(
        plan_id="p1",
        task_specs=(_spec("a"), _spec("b"), _spec("c")),
        dependencies=(
            Dependency(predecessor="a", successor="b"),
            Dependency(predecessor="b", successor="c"),
            Dependency(predecessor="c", successor="a"),
        ),
    )
    assert graph.has_cycle()


@pytest.mark.unit
def test_plangraph_acyclic_has_no_cycle_and_reports_depth() -> None:
    graph = PlanGraph(
        plan_id="p1",
        task_specs=(_spec("a", depth=1), _spec("b", depth=2), _spec("c", depth=3)),
        dependencies=(
            Dependency(predecessor="a", successor="b", kind=DependencyKind.DATA),
            Dependency(predecessor="b", successor="c"),
        ),
    )
    assert not graph.has_cycle()
    assert graph.max_depth == 3
    assert graph.successors("a") == ("b",)
    assert graph.predecessors("c") == ("b",)


# --- Evidence Join ----------------------------------------------------------


def _evidence(
    eid: str,
    source_id: str,
    kind: SourceKind,
    citation: str | None = None,
    content: str | None = "x",
) -> Evidence:
    return Evidence(
        evidence_id=eid,
        source=SourceIdentity(source_id=source_id, source_kind=kind, uri=f"uri:{source_id}"),
        citation=citation,
        content_text=content,
    )


@pytest.mark.unit
def test_evidence_join_dedupes_by_source_identity() -> None:
    dup_a = _evidence("e1", "s1", SourceKind.RAG)
    dup_b = _evidence("e2", "s1", SourceKind.RAG)  # same dedup_key
    distinct = _evidence("e3", "s2", SourceKind.RAG)
    joined = EvidenceSet.join(
        run_id="r1", task_id="t1", evidences=(dup_a, dup_b, distinct), required=True
    )
    assert joined.independent_count == 2
    assert joined.sufficiency is Sufficiency.SUFFICIENT


@pytest.mark.unit
def test_evidence_join_flags_structural_conflict() -> None:
    a = _evidence("e1", "s1", SourceKind.RAG, citation="cite:1", content="A")
    b = _evidence("e2", "s2", SourceKind.WEB, citation="cite:1", content="B")
    joined = EvidenceSet.join(run_id="r1", task_id="t1", evidences=(a, b), required=True)
    assert joined.sufficiency is Sufficiency.CONFLICTED
    assert len(joined.conflicts) == 1


@pytest.mark.unit
def test_evidence_join_required_missing_is_insufficient() -> None:
    joined = EvidenceSet.join(run_id="r1", task_id="t1", evidences=(), required=True)
    assert joined.sufficiency is Sufficiency.INSUFFICIENT
    assert joined.missing_required


# --- Artifact ---------------------------------------------------------------


@pytest.mark.unit
def test_artifact_hash_and_verify() -> None:
    data = b"hello report"
    ref = ArtifactRef(
        artifact_id="art1",
        content_hash=hash_content(data),
        path="artifacts/report.md",
        size_bytes=len(data),
        kind=ArtifactKind.REPORT_MARKDOWN,
    )
    assert ref.verify(data)
    assert not ref.verify(b"tampered")


# --- Goal / CompletionContract ---------------------------------------------


@pytest.mark.unit
def test_goal_spec_flags_obvious_ambiguity() -> None:
    assert GoalSpec(raw_input="", objective="").is_materially_ambiguous
    assert not GoalSpec(raw_input="x", objective="summarize X").is_materially_ambiguous


@pytest.mark.unit
def test_completion_contract_amend_versions_and_preserves_history() -> None:
    contract = CompletionContract(
        criteria=(
            CompletionCriterion(
                kind=CriterionKind.DELIVERABLE,
                description="report.md",
                deliverable_path="report.md",
            ),
        ),
        deliverable_paths=("report.md",),
    )
    amended = contract.amend(actor="reviewer", reason="tighten", new_criteria=contract.criteria)
    assert amended.version == 2
    assert amended.amended_by == "reviewer"
    assert len(amended.superseded_criteria) == 1


# --- Gate / Lease lifecycle methods ----------------------------------------


@pytest.mark.unit
def test_gate_single_use_consume() -> None:
    gate = Gate(
        gate_id="g1",
        run_id="r1",
        gate_type=GateType.PLAN_APPROVAL,
        actor="user",
        role="approver",
        scope="plan",
        state_version=1,
        allowed_response_schema="{}",
        expires_at=NOW + timedelta(seconds=60),
    )
    assert gate.is_open
    responded = gate.respond(request_id="req1", actor="user", payload={"ok": True}, at=NOW)
    assert responded.state is GateState.RESPONDED
    consumed = responded.consume(at=NOW)
    assert consumed.is_consumed
    # Single-use enforcement lives in the state machine, not the model copy.
    from agents_orchestration.domain.state_machine import (
        StateTransitionError,
        assert_gate_consume,
    )

    with pytest.raises(StateTransitionError):
        assert_gate_consume(GateState.CONSUMED)


@pytest.mark.unit
def test_lease_renew_and_expiry() -> None:
    lease = Lease(
        task_id="t1",
        attempt_id="a1",
        run_id="r1",
        epoch=1,
        claimed_at=NOW,
        expires_at=NOW + timedelta(seconds=10),
    )
    assert lease.state is LeaseState.CLAIMED and lease.state.is_active
    renewed = lease.renew(NOW + timedelta(seconds=30), at=NOW)
    assert renewed.state is LeaseState.RENEWED
    assert not renewed.is_expired(NOW + timedelta(seconds=20))
    assert lease.is_expired(NOW + timedelta(seconds=11))


# --- Attempt acceptance integration with model -----------------------------


@pytest.mark.unit
def test_attempt_succeed_sets_accepted() -> None:
    from agents_orchestration.domain.execution import Attempt

    attempt = Attempt(
        attempt_id="a1",
        task_id="t1",
        run_id="r1",
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        lease_epoch=1,
        plan_version=1,
        state_version_at_dispatch=1,
        started_at=NOW,
    )
    assert attempt.state is AttemptState.DISPATCHED
    ref = ArtifactRef(artifact_id="art1", content_hash=hash_content(b"x"), path="p", size_bytes=1)
    done = attempt.succeed(ref, at=NOW)
    assert done.state is AttemptState.SUCCEEDED
    assert done.acceptance is AttemptAcceptance.ACCEPTED
    assert done.finished_at == NOW


@pytest.mark.unit
def test_runpolicy_within_and_tightening() -> None:
    limits = SystemLimits()
    policy = RunPolicy.from_limits(limits, max_concurrency=2)
    assert policy.within(limits)
    loose = RunPolicy.from_limits(limits, max_concurrency=limits.max_concurrency + 1)
    assert not loose.within(limits)


@pytest.mark.unit
def test_branch_role_and_capability_kind_present() -> None:
    # Sanity that enums used across join/router are importable and distinct.
    assert BranchRole.REQUIRED is not BranchRole.OPTIONAL
    assert FailureCode.TIMEOUT.retryable
    assert not FailureCode.UNAUTHORIZED.retryable

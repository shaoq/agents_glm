"""Integration tests for parallel research and Evidence Join (Section 8)."""

from __future__ import annotations

import asyncio
import time

import pytest

from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import BranchRole, CapabilityKind, Sufficiency
from agents_orchestration.domain.evidence import Evidence, SourceIdentity, SourceKind
from agents_orchestration.orchestration.branches import (
    Branch,
    EvidenceJoiner,
    JoinPolicy,
    dispatch_branches,
    normalize_evidence,
)


def _req(bid: str, kind: CapabilityKind) -> CapabilityRequest:
    return CapabilityRequest(
        request_id=f"req-{bid}",
        capability_id=f"test::{kind.value}",
        worker_id="w",
        run_id="r1",
        task_id="t1",
        attempt_id="a1",
        inputs={},
    )


def _ev(
    eid: str,
    kind: SourceKind,
    citation: str | None = None,
    content: str = "x",
    trust=0.7,
    untrusted=True,
) -> Evidence:
    return Evidence(
        evidence_id=eid,
        source=SourceIdentity(source_id=f"s-{eid}", source_kind=kind, uri=f"uri-{eid}"),
        content_text=content,
        citation=citation,
        trust=trust,
        is_untrusted=untrusted,
    )


def _accepted(bid: str, role: BranchRole, kind: CapabilityKind, evidences=()) -> Branch:
    result = CapabilityResult.ok(
        operation_id=f"op-{bid}",
        evidence=tuple(evidences),
        source=SourceIdentity(source_id=f"s-{bid}", source_kind=SourceKind.RAG, uri=f"u-{bid}"),
    )
    return Branch(
        branch_id=bid,
        task_id="t1",
        role=role,
        capability_kind=kind,
        request=_req(bid, kind),
    ).accept(result)


def _failed(bid: str, role: BranchRole, kind: CapabilityKind) -> Branch:
    return Branch(
        branch_id=bid, task_id="t1", role=role, capability_kind=kind, request=_req(bid, kind)
    )


# --- 8.1 Branch identity + roles --------------------------------------------


@pytest.mark.integration
def test_branch_accept_records_result_and_role() -> None:
    branch = _accepted(
        "b1", BranchRole.REQUIRED, CapabilityKind.RAG_SEARCH, [_ev("e1", SourceKind.RAG)]
    )
    assert branch.accepted and branch.accepted_result is not None
    assert branch.role is BranchRole.REQUIRED


# --- 8.4 untrusted stamping -------------------------------------------------


@pytest.mark.integration
def test_normalize_marks_web_and_model_evidence_untrusted() -> None:
    web_result = CapabilityResult.ok(
        operation_id="op", evidence=(_ev("w1", SourceKind.WEB, untrusted=False),)
    )
    normalized = normalize_evidence(web_result, CapabilityKind.WEB_RESEARCH)
    assert normalized[0].is_untrusted is True


# --- 8.2 concurrent dispatch ------------------------------------------------


@pytest.mark.integration
async def test_dispatch_branches_runs_concurrently() -> None:
    branches = [
        _accepted(f"b{i}", BranchRole.OPTIONAL, CapabilityKind.MEMORY_RECALL) for i in range(3)
    ]

    async def invoke(kind, request):
        await asyncio.sleep(0.1)
        return CapabilityResult.ok(operation_id="op")

    start = time.perf_counter()
    await dispatch_branches(branches, invoke)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.25  # 3 x 0.1s must overlap, not 0.3s


# --- 8.5 / 8.6 / 8.7 dedup, conflict, sufficiency ---------------------------


@pytest.mark.integration
def test_join_dedupes_and_reports_sufficient() -> None:
    branches = (
        _accepted(
            "mem", BranchRole.OPTIONAL, CapabilityKind.MEMORY_RECALL, [_ev("m1", SourceKind.MEMORY)]
        ),
        _accepted(
            "rag", BranchRole.REQUIRED, CapabilityKind.RAG_SEARCH, [_ev("r1", SourceKind.RAG)]
        ),
    )
    joined, degradations = EvidenceJoiner().join(
        run_id="r1",
        task_id="t1",
        branches=branches,
        policy=JoinPolicy.default(),
    )
    assert joined.independent_count == 2
    assert joined.sufficiency is Sufficiency.SUFFICIENT
    assert degradations == ()


@pytest.mark.integration
def test_join_preserves_material_conflict() -> None:
    branches = (
        _accepted(
            "rag",
            BranchRole.REQUIRED,
            CapabilityKind.RAG_SEARCH,
            [
                _ev("r1", SourceKind.RAG, citation="cite:1", content="A"),
                _ev("r2", SourceKind.WEB, citation="cite:1", content="B"),
            ],
        ),
    )
    joined, degradations = EvidenceJoiner().join(
        run_id="r1",
        task_id="t1",
        branches=branches,
        policy=JoinPolicy.default(),
    )
    assert joined.sufficiency is Sufficiency.CONFLICTED
    assert any(d.flag == "conflict_unresolved" for d in degradations)


@pytest.mark.integration
def test_join_required_missing_is_insufficient() -> None:
    branches = (_failed("rag", BranchRole.REQUIRED, CapabilityKind.RAG_SEARCH),)
    joined, degradations = EvidenceJoiner().join(
        run_id="r1",
        task_id="t1",
        branches=branches,
        policy=JoinPolicy.default(),
    )
    assert joined.sufficiency is Sufficiency.INSUFFICIENT
    assert any(d.flag == "required_lane_failed" for d in degradations)


# --- 8.8 aggregation policy -------------------------------------------------


@pytest.mark.integration
def test_optional_failure_degrades_but_keeps_required_evidence() -> None:
    branches = (
        _accepted(
            "rag", BranchRole.REQUIRED, CapabilityKind.RAG_SEARCH, [_ev("r1", SourceKind.RAG)]
        ),
        _failed("mem", BranchRole.OPTIONAL, CapabilityKind.MEMORY_RECALL),
    )
    joined, degradations = EvidenceJoiner().join(
        run_id="r1",
        task_id="t1",
        branches=branches,
        policy=JoinPolicy.default(),
    )
    assert joined.sufficiency is Sufficiency.SUFFICIENT
    assert any(d.flag == "optional_lane_failed" for d in degradations)


# --- 8.9 accepted branches are not repeated --------------------------------


@pytest.mark.integration
def test_failed_branch_does_not_invalidate_accepted_branch() -> None:
    # Join reads only accepted results; a failed Optional lane never forces the
    # accepted Required lane to re-run.
    accepted = _accepted(
        "rag", BranchRole.REQUIRED, CapabilityKind.RAG_SEARCH, [_ev("r1", SourceKind.RAG)]
    )
    failed = _failed("web", BranchRole.OPTIONAL, CapabilityKind.WEB_RESEARCH)
    joined, _ = EvidenceJoiner().join(
        run_id="r1",
        task_id="t1",
        branches=(accepted, failed),
        policy=JoinPolicy.default(),
    )
    assert joined.independent_count == 1 and joined.sufficiency is Sufficiency.SUFFICIENT

"""Unit tests for PlanValidator research-only enforcement.

Covers the deterministic guard added by remove-noop-phase-tasks (task 1.3):
every dispatchable TaskSpec MUST be ``evidence_researcher``. Custom or
direct-constructed proposals that violate the invariant are rejected before
materialization, independent of LLM prompt compliance.
"""

from __future__ import annotations

import pytest

from agents_orchestration.domain.enums import WorkerRole
from agents_orchestration.domain.goal import CompletionContract
from agents_orchestration.domain.plan import TaskSpec
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.planner import PlanValidator
from agents_orchestration.orchestration.proposals import PlanProposal


def _validate(task_specs):
    proposal = PlanProposal(run_id="r1", plan_id="p1", task_specs=tuple(task_specs))
    return PlanValidator(SystemLimits()).validate(
        proposal,
        policy=RunPolicy.from_limits(SystemLimits()),
        allowed_capabilities=frozenset(),
        completion=CompletionContract(criteria=(), deliverable_paths=()),
    )


@pytest.mark.unit
def test_validator_accepts_evidence_researcher_proposal() -> None:
    validation = _validate(
        [TaskSpec(task_id="t1", worker_role=WorkerRole.EVIDENCE_RESEARCHER, description="gather")]
    )
    assert validation.accepted


@pytest.mark.unit
def test_validator_rejects_non_research_role() -> None:
    validation = _validate(
        [TaskSpec(task_id="t1", worker_role=WorkerRole.REPORT_WRITER, description="write")]
    )
    assert not validation.accepted
    assert any("non-research role" in d for d in validation.diagnostics)

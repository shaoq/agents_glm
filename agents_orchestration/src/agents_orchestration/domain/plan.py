"""Dynamic PlanGraph and Task specifications (design Decision 3/8/10).

A ``PlanGraph`` is the **dynamic research dispatch plan**: it contains only
``evidence_researcher`` TaskSpecs that the Task Runtime schedules while the Run
is ``RESEARCHING``. The downstream phases ANALYZE → WRITE → REVIEW → FINALIZE
are a **fixed lifecycle** driven by coordinator-owned phase ports
(``LLMAnalyst`` / ``LLMReportWriter`` / ``LLMReportReviewer`` / ``Finalizer``)
and are NOT represented as Tasks in this graph (remove-noop-phase-tasks).
PLAN_APPROVAL therefore approves the dynamic research scope, while the control
surface separately identifies the fixed later lifecycle.

The Planner emits a PlanGraph Proposal; the deterministic PlanValidator checks
DAG, cycle, registry, permissions, budget, depth, and the evidence_researcher-only
role invariant before acceptance. This module holds the immutable structure plus
the cheap graph invariants used by validation; full validation is in Section 5.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_orchestration.domain.enums import BranchRole, CapabilityKind, WorkerRole
from agents_orchestration.domain.ids import PlanId, TaskId


class DependencyKind(StrEnum):
    """Edge kind in the PlanGraph."""

    DATA = "data"
    CONTROL = "control"


class Dependency(BaseModel):
    """A directed edge ``predecessor -> successor``."""

    model_config = ConfigDict(frozen=True)

    predecessor: TaskId
    successor: TaskId
    kind: DependencyKind = DependencyKind.DATA


class PlanAcceptance(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ResearchExecutionMode(StrEnum):
    """Persisted consumer selected for a research Plan version.

    ``FIXED_FANOUT`` is deliberately the default so Plan JSON written before
    schema version 1 remains readable with its original semantics.
    """

    FIXED_FANOUT = "fixed_fanout"
    AGENT_LOOP = "agent_loop"


class SeedExplorationBoundary(BaseModel):
    """Per-seed limits inside an approved exploration boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: TaskId
    required_coverage: tuple[CapabilityKind, ...] = Field(min_length=1)
    max_steps: int = Field(ge=1)
    max_directions: int = Field(ge=1)
    max_tokens: int | None = Field(default=None, ge=0)
    max_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))


class ExplorationBoundary(BaseModel):
    """Versioned, immutable scope approved for adaptive research."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    allowed_capabilities: tuple[CapabilityKind, ...] = Field(min_length=1)
    seeds: tuple[SeedExplorationBoundary, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_seed_limits(self) -> ExplorationBoundary:
        allowed = set(self.allowed_capabilities)
        if len(allowed) != len(self.allowed_capabilities):
            raise ValueError("allowed capabilities must be unique")
        seed_ids = [seed.task_id for seed in self.seeds]
        if len(set(seed_ids)) != len(seed_ids):
            raise ValueError("seed task ids must be unique")
        for seed in self.seeds:
            if not set(seed.required_coverage).issubset(allowed):
                raise ValueError(
                    f"required coverage for seed {seed.task_id} must be a subset "
                    "of allowed capabilities"
                )
        return self

    def for_seed(self, task_id: TaskId) -> SeedExplorationBoundary | None:
        return next((seed for seed in self.seeds if seed.task_id == task_id), None)


class TaskSpec(BaseModel):
    """An LLM-proposed task within a PlanGraph (design Decision 3).

    Identifiers are stable: Retry does not change ``task_id``; Replan preserves
    unaffected TaskSpecs verbatim and supersedes only invalidated ones.
    """

    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    worker_role: WorkerRole
    description: str
    required_capabilities: tuple[CapabilityKind, ...] = Field(default_factory=tuple)
    branch_role: BranchRole | None = None
    deliverable_path: str | None = None
    depth: int = Field(default=1, ge=1)
    depends_on: tuple[TaskId, ...] = Field(default_factory=tuple)
    prompt_hint: str | None = None

    @property
    def is_research(self) -> bool:
        return self.worker_role is WorkerRole.EVIDENCE_RESEARCHER


class PlanGraph(BaseModel):
    """An immutable, versioned dynamic plan (design Decision 3/8)."""

    model_config = ConfigDict(frozen=True)

    plan_id: PlanId
    schema_version: int = Field(default=1, ge=1)
    research_execution_mode: ResearchExecutionMode = ResearchExecutionMode.FIXED_FANOUT
    exploration_boundary: ExplorationBoundary | None = None
    version: int = Field(default=1, ge=1)
    task_specs: tuple[TaskSpec, ...] = Field(default_factory=tuple)
    dependencies: tuple[Dependency, ...] = Field(default_factory=tuple)
    deliverable_paths: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_mode_boundary(self) -> PlanGraph:
        if (
            self.research_execution_mode is ResearchExecutionMode.AGENT_LOOP
            and self.exploration_boundary is None
        ):
            raise ValueError("agent_loop requires exploration_boundary")
        return self

    @property
    def task_ids(self) -> tuple[TaskId, ...]:
        return tuple(spec.task_id for spec in self.task_specs)

    @property
    def task_count(self) -> int:
        return len(self.task_specs)

    @property
    def max_depth(self) -> int:
        return max((spec.depth for spec in self.task_specs), default=0)

    def spec(self, task_id: TaskId) -> TaskSpec | None:
        return next((s for s in self.task_specs if s.task_id == task_id), None)

    def successors(self, task_id: TaskId) -> tuple[TaskId, ...]:
        return tuple(dep.successor for dep in self.dependencies if dep.predecessor == task_id)

    def predecessors(self, task_id: TaskId) -> tuple[TaskId, ...]:
        return tuple(dep.predecessor for dep in self.dependencies if dep.successor == task_id)

    def has_cycle(self) -> bool:
        """Cheap cycle detection via DFS coloring (full validation in 5.5)."""

        graph: dict[str, list[str]] = {tid: [] for tid in self.task_ids}
        for dep in self.dependencies:
            if dep.predecessor in graph and dep.successor in graph:
                graph[dep.predecessor].append(dep.successor)

        color = {tid: 0 for tid in self.task_ids}  # 0=white,1=gray,2=black

        def visit(node: str) -> bool:
            color[node] = 1
            for nxt in graph[node]:
                if color[nxt] == 1:
                    return True
                if color[nxt] == 0 and visit(nxt):
                    return True
            color[node] = 2
            return False

        return any(color[n] == 0 and visit(n) for n in self.task_ids)

    def approval_hash(self) -> str:
        """Stable hash of the exact seed/mode/boundary candidate being approved."""

        payload = self.model_dump_json(exclude_none=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Plan(BaseModel):
    """Stored Plan record: a PlanGraph plus acceptance metadata."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    graph: PlanGraph
    acceptance: PlanAcceptance = PlanAcceptance.PROPOSED
    proposed_at: datetime
    accepted_at: datetime | None = None
    rejected_diagnostics: tuple[str, ...] = Field(default_factory=tuple)
    superseded_by: int | None = None

    @property
    def version(self) -> int:
        return self.graph.version

    def accept(self, at: datetime) -> Plan:
        return self.model_copy(update={"acceptance": PlanAcceptance.ACCEPTED, "accepted_at": at})

    def reject(self, diagnostics: tuple[str, ...], at: datetime) -> Plan:
        return self.model_copy(
            update={
                "acceptance": PlanAcceptance.REJECTED,
                "rejected_diagnostics": diagnostics,
                "accepted_at": at,
            }
        )

    def supersede(self, by_version: int) -> Plan:
        return self.model_copy(
            update={"acceptance": PlanAcceptance.SUPERSEDED, "superseded_by": by_version}
        )

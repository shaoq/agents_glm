"""Semantic Proposal types and Model Ports (tasks 5.1 / 5.4).

LLM-assisted components (GoalNormalizer, Planner, Reviewer) may ONLY emit these
Proposal value objects. Deterministic components validate them and commit formal
state — the model can never bypass policy, the state machine or the budget
(design Decision 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.goal import CompletionContract, GoalSpec
from agents_orchestration.domain.ids import RunId
from agents_orchestration.domain.plan import (
    Dependency,
    ExplorationBoundary,
    ResearchExecutionMode,
    TaskSpec,
)


class GoalClarificationProposal(BaseModel):
    """Emitted when the raw goal is materially ambiguous (task 5.3)."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    ambiguities: tuple[str, ...]
    questions: tuple[str, ...]


class PlanProposal(BaseModel):
    """A Planner-proposed dynamic plan, parsed but NOT yet materialized (5.4).

    Constructing this object has no Task-materialization side effects; the
    deterministic :class:`PlanValidator` + :class:`PlanAcceptor` decide whether
    any of it becomes formal state.
    """

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    plan_id: str
    schema_version: int = Field(default=1, ge=1)
    research_execution_mode: ResearchExecutionMode = ResearchExecutionMode.FIXED_FANOUT
    exploration_boundary: ExplorationBoundary | None = None
    task_specs: tuple[TaskSpec, ...] = Field(default_factory=tuple)
    dependencies: tuple[Dependency, ...] = Field(default_factory=tuple)
    deliverable_paths: tuple[str, ...] = Field(default_factory=tuple)

    def to_graph(self, version: int):
        from agents_orchestration.domain.plan import PlanGraph

        return PlanGraph(
            plan_id=self.plan_id,
            schema_version=self.schema_version,
            research_execution_mode=self.research_execution_mode,
            exploration_boundary=self.exploration_boundary,
            version=version,
            task_specs=self.task_specs,
            dependencies=self.dependencies,
            deliverable_paths=self.deliverable_paths,
        )


class ReplanProposal(BaseModel):
    """A bounded Replan request (task 5.9)."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    reason: str
    invalidate_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    add_task_specs: tuple[TaskSpec, ...] = Field(default_factory=tuple)
    add_dependencies: tuple[Dependency, ...] = Field(default_factory=tuple)


@dataclass(frozen=True)
class GoalNormalizationOutcome:
    goal: GoalSpec
    completion: CompletionContract
    clarification: GoalClarificationProposal | None


@runtime_checkable
class GoalNormalizer(Protocol):
    """Model-backed goal normalization (task 5.1). Emits Proposals only."""

    async def normalize(self, raw_goal: str, run_id: str) -> GoalNormalizationOutcome: ...


@runtime_checkable
class Planner(Protocol):
    """Model-backed dynamic planner (task 5.1). Emits a PlanProposal only."""

    async def propose_plan(
        self, goal: GoalSpec, completion: CompletionContract, run_id: str
    ) -> PlanProposal: ...

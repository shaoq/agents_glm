"""Goal normalization outputs: GoalSpec, CompletionContract, criteria.

The GoalNormalizer (task 5.1) emits a ``GoalSpec`` Proposal; once accepted, a
versioned ``CompletionContract`` becomes the deterministic success boundary used
by Final Verification (task 10.6).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CriterionKind(StrEnum):
    """Kinds of Completion Contract criteria."""

    DELIVERABLE = "deliverable"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"
    REQUIRED_SOURCE = "required_source"
    CITATION_INTEGRITY = "citation_integrity"
    CUSTOM = "custom"


class CompletionCriterion(BaseModel):
    """A single deterministic success criterion."""

    model_config = ConfigDict(frozen=True)

    kind: CriterionKind
    description: str
    required: bool = True
    deliverable_path: str | None = None
    min_evidence_count: int | None = Field(default=None, ge=0)


class GoalSpec(BaseModel):
    """Normalized research goal emitted by the GoalNormalizer."""

    model_config = ConfigDict(frozen=True)

    raw_input: str
    objective: str
    scope: tuple[str, ...] = Field(default_factory=tuple)
    constraints: tuple[str, ...] = Field(default_factory=tuple)
    deliverables: tuple[str, ...] = Field(default=("report.md",))
    language: str = "zh"
    clarifications: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def is_materially_ambiguous(self) -> bool:
        """Heuristic flag consumed by material ambiguity detection (task 5.3).

        Real ambiguity detection is model-assisted; this structural flag only
        catches the obvious "no objective / no deliverables" case so the
        deterministic layer can force a GOAL_CLARIFICATION gate without a model.
        """

        return not self.objective.strip() or not self.deliverables


class CompletionContract(BaseModel):
    """Versioned success boundary. Amendment is additive and attributed."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(default=1, ge=1)
    criteria: tuple[CompletionCriterion, ...] = Field(default_factory=tuple)
    deliverable_paths: tuple[str, ...] = Field(default_factory=tuple)
    amended_by: str | None = None
    amend_reason: str | None = None
    superseded_criteria: tuple[CompletionCriterion, ...] = Field(default_factory=tuple)

    def amend(
        self,
        *,
        actor: str,
        reason: str,
        new_criteria: tuple[CompletionCriterion, ...],
        deliverable_paths: tuple[str, ...] | None = None,
    ) -> CompletionContract:
        """Return a new versioned contract with ``new_criteria`` replacing.

        Old criteria move to ``superseded_criteria`` so Final Verification can
        prove invalidated validations (task 5.8).
        """

        return CompletionContract(
            version=self.version + 1,
            criteria=new_criteria,
            deliverable_paths=deliverable_paths
            if deliverable_paths is not None
            else self.deliverable_paths,
            amended_by=actor,
            amend_reason=reason,
            superseded_criteria=self.superseded_criteria + self.criteria,
        )

    @property
    def required_criteria(self) -> tuple[CompletionCriterion, ...]:
        return tuple(c for c in self.criteria if c.required)

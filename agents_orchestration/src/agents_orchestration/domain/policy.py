"""System limits, run policy and shared budget (design Decision 8/10).

``SystemLimits`` are the hard maximums the runtime enforces. ``RunPolicy`` is a
per-Run tightening of those limits (it may only stay within or tighten system
policy — validated in task 12.2). ``Budget`` is shared across Retry, Replan and
Report revision and is never reset by replanning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SystemLimits(BaseModel):
    """Hard system-wide maximums. Matches the design's limits table."""

    model_config = ConfigDict(frozen=True)

    max_tasks: int = Field(default=32, ge=1)
    max_plan_depth: int = Field(default=4, ge=1)
    max_concurrency: int = Field(default=4, ge=1)
    max_attempts_per_task: int = Field(default=3, ge=1)
    max_replans: int = Field(default=2, ge=0)
    max_report_revisions: int = Field(default=2, ge=0)
    max_research_steps_per_seed: int = Field(default=12, ge=1)
    max_research_directions_per_seed: int = Field(default=6, ge=1)
    max_research_tokens_per_seed: int = Field(default=100_000, ge=0)
    max_research_cost_usd_per_seed: Decimal = Field(default=Decimal("100"), ge=Decimal("0"))
    default_run_deadline_seconds: int = Field(default=1800, gt=0)


class RunPolicy(BaseModel):
    """Per-Run policy. Must remain within (or tighten) :class:`SystemLimits`."""

    model_config = ConfigDict(frozen=True)

    deadline_seconds: int = Field(gt=0)
    max_tasks: int = Field(ge=1)
    max_plan_depth: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    max_attempts_per_task: int = Field(ge=1)
    max_replans: int = Field(ge=0)
    max_report_revisions: int = Field(ge=0)
    max_research_steps_per_seed: int = Field(ge=1)
    max_research_directions_per_seed: int = Field(ge=1)
    max_research_tokens_per_seed: int = Field(ge=0)
    max_research_cost_usd_per_seed: Decimal = Field(ge=Decimal("0"))
    web_enabled: bool = False
    web_allowed_domains: tuple[str, ...] = Field(default_factory=tuple)

    @classmethod
    def from_limits(cls, limits: SystemLimits, **overrides: object) -> RunPolicy:
        """Build a RunPolicy from system limits, optionally tightening fields."""

        base = {
            "deadline_seconds": limits.default_run_deadline_seconds,
            "max_tasks": limits.max_tasks,
            "max_plan_depth": limits.max_plan_depth,
            "max_concurrency": limits.max_concurrency,
            "max_attempts_per_task": limits.max_attempts_per_task,
            "max_replans": limits.max_replans,
            "max_report_revisions": limits.max_report_revisions,
            "max_research_steps_per_seed": limits.max_research_steps_per_seed,
            "max_research_directions_per_seed": limits.max_research_directions_per_seed,
            "max_research_tokens_per_seed": limits.max_research_tokens_per_seed,
            "max_research_cost_usd_per_seed": limits.max_research_cost_usd_per_seed,
        }
        base.update(overrides)
        return cls(**base)  # type: ignore[arg-type]

    def within(self, limits: SystemLimits) -> bool:
        """True if this policy never exceeds ``limits`` (task 12.2)."""

        return (
            self.deadline_seconds <= limits.default_run_deadline_seconds
            and self.max_tasks <= limits.max_tasks
            and self.max_plan_depth <= limits.max_plan_depth
            and self.max_concurrency <= limits.max_concurrency
            and self.max_attempts_per_task <= limits.max_attempts_per_task
            and self.max_replans <= limits.max_replans
            and self.max_report_revisions <= limits.max_report_revisions
            and self.max_research_steps_per_seed <= limits.max_research_steps_per_seed
            and self.max_research_directions_per_seed <= limits.max_research_directions_per_seed
            and self.max_research_tokens_per_seed <= limits.max_research_tokens_per_seed
            and self.max_research_cost_usd_per_seed <= limits.max_research_cost_usd_per_seed
        )


class Budget(BaseModel):
    """Shared Run budget. Immutable; ``consume`` returns a new instance.

    Retry, Replan and Report revision all draw from the same budget and the
    deadline is never extended (design Decision 10).
    """

    model_config = ConfigDict(frozen=True)

    deadline_at: datetime | None = None
    max_tokens: int | None = Field(default=None, ge=0)
    max_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    tokens_used: int = Field(default=0, ge=0)
    cost_usd_used: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

    @model_validator(mode="after")
    def _validate_usage(self) -> Budget:
        if self.max_tokens is not None and self.tokens_used > self.max_tokens:
            raise ValueError("token budget exhausted")
        if self.max_cost_usd is not None and self.cost_usd_used > self.max_cost_usd:
            raise ValueError("cost budget exhausted")
        return self

    def consume(
        self,
        *,
        tokens: int = 0,
        cost_usd: Decimal | float | int = Decimal("0"),
    ) -> Budget:
        """Return a new Budget with ``tokens`` / ``cost_usd`` consumed."""

        cost = cost_usd if isinstance(cost_usd, Decimal) else Decimal(str(cost_usd))
        return Budget.model_validate(
            {
                **self.model_dump(),
                "tokens_used": self.tokens_used + tokens,
                "cost_usd_used": self.cost_usd_used + cost,
            }
        )

    def deadline_exceeded(self, now: datetime) -> bool:
        return self.deadline_at is not None and now >= self.deadline_at

    def exhausted(self, now: datetime | None = None) -> bool:
        if self.deadline_at is not None and now is not None and now >= self.deadline_at:
            return True
        if self.max_tokens is not None and self.tokens_used >= self.max_tokens:
            return True
        if self.max_cost_usd is not None and self.cost_usd_used >= self.max_cost_usd:
            return True
        return False

    @classmethod
    def from_deadline(cls, deadline_at: datetime) -> Budget:
        return cls(deadline_at=deadline_at.replace(tzinfo=deadline_at.tzinfo or UTC))

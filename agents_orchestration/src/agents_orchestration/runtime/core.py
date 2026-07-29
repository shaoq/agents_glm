"""Deterministic runtime core: Scheduler, BudgetGuard, RetryClassifier,
CheckpointService (tasks 4.1 / 4.3 / 4.4 / 4.5).

These operate purely on the UnitOfWork + domain, so they are fully testable
without a capability stack. They never call a provider; the Tick (4.9) composes
them with an injected TaskExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from agents_orchestration.domain.enums import FailureCode, TaskState
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.lifecycle import Checkpoint, CheckpointKind
from agents_orchestration.domain.policy import SystemLimits


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a :class:`BudgetGuard` check."""

    allowed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def ok(cls) -> BudgetDecision:
        return cls(allowed=True)

    @classmethod
    def deny(cls, *violations: str) -> BudgetDecision:
        return cls(allowed=False, violations=tuple(violations))


class Scheduler:
    """Computes Ready Work from formal Task and dependency state (task 4.1)."""

    def __init__(self, uow) -> None:
        self.uow = uow

    def ready_work(self, run: Run, *, max_concurrency: int) -> list[Task]:
        if run.current_plan_version is None:
            return []
        plan_version = run.current_plan_version
        tasks = self.uow.tasks.by_run(run.run_id, plan_version)
        accepted = {t.task_id for t in tasks if t.state.is_accepted}
        ready: list[Task] = []
        for task in tasks:
            if task.state is TaskState.PENDING and all(dep in accepted for dep in task.depends_on):
                ready.append(task)
            elif task.state is TaskState.READY:
                ready.append(task)
        ready.sort(key=lambda t: (t.depth, t.task_id))
        return ready[: max(0, max_concurrency)]


class BudgetGuard:
    """Enforces deadline, count, depth, concurrency and budget limits (task 4.3)."""

    def __init__(self, limits: SystemLimits) -> None:
        self.limits = limits

    def check_plan(
        self, *, task_count: int, max_depth: int, policy_max_tasks: int, policy_max_depth: int
    ) -> BudgetDecision:
        violations: list[str] = []
        if task_count > policy_max_tasks:
            violations.append(f"task_count {task_count} > policy max {policy_max_tasks}")
        if task_count > self.limits.max_tasks:
            violations.append(f"task_count {task_count} > system max {self.limits.max_tasks}")
        if max_depth > policy_max_depth:
            violations.append(f"depth {max_depth} > policy max {policy_max_depth}")
        if max_depth > self.limits.max_plan_depth:
            violations.append(f"depth {max_depth} > system max {self.limits.max_plan_depth}")
        return BudgetDecision.ok() if not violations else BudgetDecision.deny(*violations)

    def check_run(self, run: Run, *, now, dispatched_count: int) -> BudgetDecision:
        violations: list[str] = []
        if run.budget.deadline_at is not None and now >= run.budget.deadline_at:
            violations.append("deadline_exceeded")
        if run.replan_count > run.policy.max_replans:
            violations.append("replan_budget_exceeded")
        if run.revision_count > run.policy.max_report_revisions:
            violations.append("revision_budget_exceeded")
        if dispatched_count > run.policy.max_concurrency:
            violations.append("concurrency_exceeded")
        if run.budget.max_tokens is not None and run.budget.tokens_used >= run.budget.max_tokens:
            violations.append("token_budget_exceeded")
        if (
            run.budget.max_cost_usd is not None
            and run.budget.cost_usd_used >= run.budget.max_cost_usd
        ):
            violations.append("cost_budget_exceeded")
        return BudgetDecision.ok() if not violations else BudgetDecision.deny(*violations)


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    backoff_seconds: float
    reason: str


def retry_backoff_seconds(attempt_count: int, *, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff ``base * 2^(attempt_count-1)``, capped at ``cap``.

    Shared by :class:`RetryClassifier` (backoff on failure) and the Tick's
    retry-readmit step (AWAITING_RETRY → READY), so the two never diverge.
    """

    return min(cap, base * (2 ** (attempt_count - 1)))


class RetryClassifier:
    """Durable retry classification with bounded budget and backoff (task 4.4)."""

    def __init__(self, limits: SystemLimits, *, base_backoff_seconds: float = 1.0) -> None:
        self.limits = limits
        self.base_backoff = base_backoff_seconds

    def classify(
        self, *, failure_code: FailureCode, attempts_used: int, max_attempts: int
    ) -> RetryDecision:
        if attempts_used >= max_attempts:
            return RetryDecision(False, 0.0, "attempts_exhausted")
        if not failure_code.retryable:
            return RetryDecision(False, 0.0, f"{failure_code.value}_not_retryable")
        backoff = retry_backoff_seconds(attempts_used, base=self.base_backoff)
        return RetryDecision(True, backoff, "retry_with_backoff")


class CheckpointService:
    """Creates semantic checkpoints at plan/branch/gate/retry/replan/finalize (4.5)."""

    def __init__(self, uow, clock, idgen) -> None:
        self.uow = uow
        self.clock = clock
        self.idgen = idgen

    def record(
        self,
        *,
        run_id: str,
        kind: CheckpointKind,
        state_version: int,
        plan_version: int | None = None,
        artifact_hash: str | None = None,
        reason: str,
    ) -> Checkpoint:
        checkpoint = Checkpoint(
            checkpoint_id=self.idgen.new_id("ckpt"),
            run_id=run_id,
            kind=kind,
            state_version=state_version,
            plan_version=plan_version,
            artifact_hash=artifact_hash,
            reason=reason,
            created_at=self.clock.now(),
        )
        self.uow.checkpoints.save(checkpoint)
        return checkpoint


def consume_budget_safe(
    run: Run, *, tokens: int = 0, cost_usd: Decimal | float | int = Decimal("0")
) -> Run:
    """Apply budget consumption, mapping overrun to a FAILED termination signal.

    Returns the (possibly terminated) Run. Callers commit it via CAS.
    """

    try:
        new_budget = run.budget.consume(tokens=tokens, cost_usd=cost_usd)
        return run.consume_budget(new_budget, run.updated_at)
    except ValueError:
        # Budget overrun: surface as a terminal signal without bypassing CAS.
        raise BudgetOverrun(run.run_id) from None


class BudgetOverrun(RuntimeError):
    """Raised when budget consumption would overrun; the Tick maps this to termination."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


__all__ = [
    "BudgetDecision",
    "BudgetGuard",
    "BudgetOverrun",
    "CheckpointService",
    "RetryClassifier",
    "RetryDecision",
    "retry_backoff_seconds",
    "Scheduler",
    "consume_budget_safe",
]

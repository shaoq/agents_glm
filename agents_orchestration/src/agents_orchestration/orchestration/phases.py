"""Goal and Planning phase handlers (Ch.5 tasks 5.1-5.10).

These are the first concrete :class:`PhaseHandler` implementations. The Goal
phase normalizes the raw goal (async, model-backed GoalNormalizer emitting
Proposals only) and opens a GOAL_CLARIFICATION gate for material ambiguity. The
Planning phase proposes a Plan (async Planner), then deterministically
validates and accepts it via :class:`PlanValidator` / :class:`PlanAcceptor`.

Provider calls happen in ``execute`` (outside any write transaction); phase-
specific persistence happens in ``accept`` (inside the coordinator's write
transaction). Provider failures degrade to IDLE rather than crashing the Run.
"""

from __future__ import annotations

from datetime import datetime

from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    InputFingerprint,
    PhaseId,
)
from agents_orchestration.domain.enums import (
    CapabilityKind,
    EffectType,
    FailureCode,
    GateType,
    RunState,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.plan import Plan
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.orchestration.coordinator import (
    PhaseContext,
    PhaseOutcome,
    transition_or_stay,
)
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import (
    GoalNormalizationOutcome,
    GoalNormalizer,
    Planner,
)


def _fingerprint(state_version: int, plan_version: int | None = None) -> InputFingerprint:
    return InputFingerprint(state_version=state_version, plan_version=plan_version)


class GoalPhaseHandler:
    """NORMALIZING phase (task 5.3): normalize goal, open clarification gate if
    materially ambiguous, otherwise persist GoalSpec + Completion Contract."""

    phase = PhaseId.GOAL

    def __init__(self, normalizer: GoalNormalizer, idgen) -> None:
        self.normalizer = normalizer
        self.idgen = idgen

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        try:
            outcome = await self.normalizer.normalize(ctx.run.raw_goal, ctx.run.run_id)
        except Exception as exc:  # provider failure -> degrade (task 5.2)
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason=f"goal-normalizer-failed:{type(exc).__name__}",
                failure_code=FailureCode.UPSTREAM_ERROR,
                stage_logical_key="goal",
                input_fingerprint=_fingerprint(ctx.run.state_version),
            )
        if outcome.clarification is not None:
            return PhaseOutcome(
                disposition=AdvanceDisposition.BLOCKED,
                reason="goal-ambiguous",
                open_gate=GateType.GOAL_CLARIFICATION,
                stage_logical_key="goal",
                input_fingerprint=_fingerprint(ctx.run.state_version),
                proposal=outcome,
            )
        return PhaseOutcome(
            disposition=AdvanceDisposition.PROGRESSED,
            next_state=RunState.PLANNING,
            reason="goal-normalized",
            stage_logical_key="goal",
            input_fingerprint=_fingerprint(ctx.run.state_version),
            proposal=outcome,
        )

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now: datetime) -> Run:
        norm: GoalNormalizationOutcome = outcome.proposal  # type: ignore[assignment]
        uow.goals.save(run.run_id, norm.goal)
        uow.completion.save(run.run_id, norm.completion)
        moved = transition_or_stay(run, outcome.next_state, now)
        if moved.state is not run.state:
            uow.runs.save(moved, expected_version=run.state_version)
        uow.events.append([
            DomainEvent(
                event_id=self.idgen.new_id("evt"),
                run_id=run.run_id,
                effect=EffectType.GOAL_NORMALIZED,
                state_version=moved.state_version,
                occurred_at=now,
                payload={"objective": norm.goal.objective},
            )
        ])
        return moved


class PlanningPhaseHandler:
    """PLANNING phase (tasks 5.7/5.8): propose, validate, accept a Plan.

    ``execute`` reads the accepted Goal/Contract, invokes the Planner, and
    pre-validates so an invalid proposal degrades to IDLE without entering the
    write transaction. ``accept`` re-validates against current state (stale-
    safe) and materializes the Plan + Tasks via :class:`PlanAcceptor`.
    """

    phase = PhaseId.PLAN

    def __init__(
        self,
        planner: Planner,
        *,
        limits: SystemLimits,
        allowed_capabilities: frozenset[CapabilityKind],
        clock,
        idgen,
        approval_required: bool = False,
    ) -> None:
        self.planner = planner
        self.validator = PlanValidator(limits)
        self.allowed_capabilities = allowed_capabilities
        self.clock = clock
        self.idgen = idgen
        self.approval_required = approval_required

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        with backend.unit_of_work() as uow:
            goal = uow.goals.get(ctx.run.run_id)
            completion = uow.completion.get(ctx.run.run_id)
            uow.commit()
        if goal is None or completion is None:
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason="goal-or-contract-missing",
                stage_logical_key="plan",
            )
        try:
            proposal = await self.planner.propose_plan(goal, completion, ctx.run.run_id)
        except Exception as exc:  # provider failure -> degrade
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason=f"planner-failed:{type(exc).__name__}",
                failure_code=FailureCode.UPSTREAM_ERROR,
                stage_logical_key="plan",
                input_fingerprint=_fingerprint(ctx.run.state_version, ctx.run.current_plan_version),
            )
        validation = self.validator.validate(
            proposal,
            policy=ctx.run.policy,
            allowed_capabilities=self.allowed_capabilities,
            completion=completion,
        )
        if not validation.accepted:  # task 5.6: invalid proposal -> no Tasks
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason="plan-invalid:" + ";".join(validation.diagnostics),
                failure_code=FailureCode.POLICY_VIOLATION,
                stage_logical_key="plan",
                input_fingerprint=_fingerprint(ctx.run.state_version, ctx.run.current_plan_version),
                proposal=proposal,
            )
        if self.approval_required:  # task 5.9: open PLAN_APPROVAL before Tasks execute
            return PhaseOutcome(
                disposition=AdvanceDisposition.BLOCKED,
                reason="plan-approval-required",
                open_gate=GateType.PLAN_APPROVAL,
                stage_logical_key="plan",
                input_fingerprint=_fingerprint(ctx.run.state_version, ctx.run.current_plan_version),
                proposal=proposal,
            )
        return PhaseOutcome(
            disposition=AdvanceDisposition.PROGRESSED,
            next_state=RunState.RESEARCHING,
            reason="plan-accepted",
            stage_logical_key="plan",
            input_fingerprint=_fingerprint(ctx.run.state_version, ctx.run.current_plan_version),
            proposal=proposal,
        )

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now: datetime) -> Run:
        proposal = outcome.proposal
        completion = uow.completion.get(run.run_id)
        validation = self.validator.validate(
            proposal,
            policy=run.policy,
            allowed_capabilities=self.allowed_capabilities,
            completion=completion,
        )
        if not validation.accepted or validation.graph is None:
            next_version = (run.current_plan_version or 0) + 1
            rejected = Plan(
                run_id=run.run_id,
                graph=proposal.to_graph(next_version),
                proposed_at=now,
            ).reject(validation.diagnostics, now)
            uow.plans.save(rejected)
            return run  # no transition; a stale/invalid proposal materializes no Tasks
        _plan, new_run = PlanAcceptor(uow, self.clock, self.idgen).accept(
            run, proposal, validation
        )
        return new_run


__all__ = [
    "GoalPhaseHandler",
    "PlanningPhaseHandler",
]

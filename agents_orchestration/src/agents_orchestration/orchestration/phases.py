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
from typing import Protocol

from agents_orchestration.domain.coordination import (
    AdvanceDisposition,
    InputFingerprint,
    PhaseId,
    TaskTickSummary,
)
from agents_orchestration.domain.enums import (
    CapabilityKind,
    EffectType,
    FailureCode,
    GateType,
    ReviewSource,
    ReviewVerdict,
    RunState,
    Sufficiency,
    SufficiencyVerdict,
    TaskState,
    TerminationReason,
    WorkerRole,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.evidence import EvidenceSet
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.lifecycle import GateContinuationIntent
from agents_orchestration.domain.plan import Plan
from agents_orchestration.domain.policy import SystemLimits
from agents_orchestration.domain.state_machine import assert_run_transition
from agents_orchestration.orchestration.coordinator import (
    PhaseContext,
    PhaseOutcome,
    transition_or_stay,
)
from agents_orchestration.orchestration.focused_replan import sanitize_gap
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import (
    GoalNormalizationOutcome,
    GoalNormalizer,
    Planner,
)
from agents_orchestration.orchestration.replan import ReplanService
from agents_orchestration.orchestration.report import (
    AnalysisArtifact,
    CompletionEvaluator,
    Finalizer,
    ReportBuilder,
    ReportContent,
    ReviewProposal,
)
from agents_orchestration.orchestration.sufficiency import (
    AnalysisSufficiencyOutcome,
    SufficiencyReview,
    SufficiencyValidationError,
    source_evidence_hash,
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
            # task 4.1: normalize the effective goal (raw goal + clarification) so a
            # clarified Gate response re-enters normalization as new phase input.
            outcome = await self.normalizer.normalize(ctx.run.effective_goal, ctx.run.run_id)
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
        uow.events.append(
            [
                DomainEvent(
                    event_id=self.idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=EffectType.GOAL_NORMALIZED,
                    state_version=moved.state_version,
                    occurred_at=now,
                    payload={"objective": norm.goal.objective},
                )
            ]
        )
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
        if completion is None:
            return run  # stale: contract missing, no Tasks materialized
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
        _plan, new_run = PlanAcceptor(uow, self.clock, self.idgen).accept(run, proposal, validation)
        return new_run

    def persist_for_approval(
        self, outcome: PhaseOutcome, run: Run, uow, now: datetime
    ) -> None:
        """Persist a validated proposal before opening its approval Gate."""

        proposal = outcome.proposal
        next_version = (run.current_plan_version or 0) + 1
        uow.plans.save(
            Plan(
                run_id=run.run_id,
                graph=proposal.to_graph(next_version),
                proposed_at=now,
            )
        )


class ResearchPhaseHandler:
    """RESEARCHING phase (tasks 6.7-6.10): drive research Tasks through the
    Task Runtime, then deterministically join accepted evidence into an
    immutable EvidenceSet before entering ANALYZING.

    ``tick`` is the bounded :class:`RuntimeTick` (phase-role filtering in 6.2
    ensures it only dispatches EVIDENCE_RESEARCHER Tasks here).
    ``evidence_provider`` loads accepted research evidence for the Join; the
    composition root (Ch.9) wires a real loader, tests inject a deterministic double.
    """

    phase = PhaseId.RESEARCH

    def __init__(self, tick, evidence_provider, *, clock, idgen) -> None:
        self.tick = tick
        self.evidence_provider = evidence_provider
        self.clock = clock
        self.idgen = idgen

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        fp = _fingerprint(ctx.run.state_version, ctx.run.current_plan_version)
        with backend.unit_of_work() as uow:
            tasks = [
                t
                for t in uow.tasks.by_run(ctx.run.run_id, plan_version=ctx.run.current_plan_version)
                if t.worker_role is WorkerRole.EVIDENCE_RESEARCHER
            ]
            uow.commit()
        if not tasks:
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason="no-research-tasks",
                stage_logical_key="research",
                input_fingerprint=fp,
            )
        if any(not t.is_terminal for t in tasks):  # 6.5: work in flight / pending
            report = await self.tick.tick(ctx.run.run_id)
            summary = TaskTickSummary(
                dispatched=report.dispatched,
                accepted=report.accepted,
                terminal=report.terminal,
            )
            disposition = (
                AdvanceDisposition.PROGRESSED
                if (report.dispatched or report.accepted)
                else AdvanceDisposition.IDLE
            )
            waiting = disposition is AdvanceDisposition.IDLE and report.blocked
            return PhaseOutcome(
                disposition=disposition,
                next_state=None,
                reason=f"research-tick:dispatched={report.dispatched}",
                stage_logical_key="research",
                input_fingerprint=fp,
                task_tick=summary,
                counts_toward_idle_budget=not waiting,
                continue_immediately=not waiting,
            )
        if all(t.state is TaskState.SUCCEEDED for t in tasks):  # 6.8: Join
            evidences = await self.evidence_provider(ctx.run.run_id)
            evidence_set = EvidenceSet.join(
                run_id=ctx.run.run_id,
                task_id="research",
                evidences=tuple(evidences),
                required=True,
            )
            return PhaseOutcome(
                disposition=AdvanceDisposition.PROGRESSED,
                next_state=RunState.ANALYZING,
                reason=f"research-joined:sufficiency={evidence_set.sufficiency.value}",
                stage_logical_key="research",
                input_fingerprint=fp,
                proposal=evidence_set,
            )
        return PhaseOutcome(  # 6.10: a required research Task failed -> degrade
            disposition=AdvanceDisposition.IDLE,
            reason="research-task-failed",
            failure_code=FailureCode.UNKNOWN,
            stage_logical_key="research",
            input_fingerprint=fp,
        )

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now) -> Run:
        moved = transition_or_stay(run, outcome.next_state, now)
        if moved.state is not run.state:
            uow.runs.save(moved, expected_version=run.state_version)
        return moved


# --- Task 7.1: role-specific model-backed ports (Proposal-only) ------------


class Analyst(Protocol):
    async def __call__(self, run_id: str, evidence: EvidenceSet) -> AnalysisArtifact: ...


class ReportWriter(Protocol):
    async def __call__(self, run_id: str, analysis: AnalysisArtifact) -> ReportContent: ...


class ReportReviewer(Protocol):
    async def __call__(self, run_id: str, report: ReportContent) -> ReviewProposal: ...


def _simple_accept(outcome: PhaseOutcome, run: Run, uow, now) -> Run:
    moved = transition_or_stay(run, outcome.next_state, now)
    if moved.state is not run.state:
        uow.runs.save(moved, expected_version=run.state_version)
    return moved


_STRUCTURAL_GAP_HINT = "No independent evidence collected for the required research."
_STRUCTURAL_RATIONALE = "Required research produced zero independent evidence (L0)."


class AnalysisPhaseHandler:
    """ANALYZING phase (analyze-sufficiency-feedback 5.3-5.5).

    L0: a required EvidenceSet with zero independent evidence (``INSUFFICIENT``)
    short-circuits to a structural ``research_gap`` WITHOUT calling the analyst
    or reviewer. Otherwise the analyst produces a candidate Analysis and the L1
    reviewer judges whether the evidence supports it. ``sufficient`` /
    ``conflict`` materialize the candidate as the authoritative artifact and
    enter WRITING; ``research_gap`` raises a plan-scoped Focused Replan (or
    terminates deterministically when the replan budget is exhausted). Provider
    failures degrade to a non-immediately-retryable IDLE.
    """

    phase = PhaseId.ANALYZE

    def __init__(
        self,
        analyst: Analyst,
        evidence_provider,
        artifact_store,
        sufficiency_reviewer,
        focused_replan_builder,
        validator: PlanValidator,
        *,
        clock,
        idgen,
    ) -> None:
        self.analyst = analyst
        self.evidence_provider = evidence_provider
        self.artifact_store = artifact_store
        self.sufficiency_reviewer = sufficiency_reviewer
        self.focused_replan_builder = focused_replan_builder
        self.validator = validator
        self.clock = clock
        self.idgen = idgen

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        fp = _fingerprint(ctx.run.state_version, ctx.run.current_plan_version)
        try:
            evidence = await self.evidence_provider(ctx.run.run_id)
        except Exception as exc:  # evidence provider failure -> degrade (5.5)
            return self._idle(fp, f"analyze-evidence-failed:{type(exc).__name__}")
        ev_hash = source_evidence_hash(evidence)

        # L0: zero independent required evidence -> structural gap, no model call.
        if evidence.sufficiency is Sufficiency.INSUFFICIENT:
            return self._gap_outcome(
                ctx, backend, fp, ev_hash, analysis=None, source=ReviewSource.STRUCTURAL
            )

        try:
            analysis = await self.analyst(ctx.run.run_id, evidence)
            review = await self.sufficiency_reviewer.review(ctx.run.run_id, analysis, evidence)
        except SufficiencyValidationError as exc:  # invalid reviewer structure (5.6)
            return self._idle(fp, f"analyze-invalid-review:{exc}")
        except Exception as exc:  # analyst/reviewer failure -> degrade (5.5)
            return self._idle(fp, f"analyze-provider-failed:{type(exc).__name__}")

        if review.verdict in (SufficiencyVerdict.SUFFICIENT, SufficiencyVerdict.CONFLICT):
            return await self._accept_outcome(ctx, fp, ev_hash, analysis, review)
        # semantic research_gap
        return self._gap_outcome(
            ctx,
            backend,
            fp,
            ev_hash,
            analysis=analysis,
            source=ReviewSource.SEMANTIC,
            gap_hint=review.gap_hint,
            rationale=review.rationale,
        )

    async def _accept_outcome(self, ctx, fp, ev_hash, analysis, review) -> PhaseOutcome:
        try:
            ref = await self.artifact_store.materialize(
                run_id=ctx.run.run_id,
                plan_version=ctx.run.current_plan_version or 0,
                analysis=analysis,
                source_evidence_hash=ev_hash,
            )
        except Exception as exc:  # artifact store failure -> degrade (5.5)
            return self._idle(fp, f"analyze-artifact-failed:{type(exc).__name__}")
        outcome = AnalysisSufficiencyOutcome(
            review=review, source_evidence_hash=ev_hash, analysis=analysis
        )
        return PhaseOutcome(
            disposition=AdvanceDisposition.PROGRESSED,
            next_state=RunState.WRITING,
            reason=f"analyze-{review.verdict.value}",
            stage_logical_key="analyze",
            input_fingerprint=fp,
            proposal=outcome,
            output_refs=(ref.as_artifact_ref(),),
            output_entities=(
                ref.artifact_id,
                f"verdict:{review.verdict.value}",
                f"source_evidence_hash:{ev_hash}",
            ),
        )

    def _gap_outcome(
        self, ctx, backend, fp, ev_hash, *, analysis, source, gap_hint=None, rationale=None
    ) -> PhaseOutcome:
        hint = gap_hint if gap_hint is not None else _STRUCTURAL_GAP_HINT
        reason_text = rationale or (
            _STRUCTURAL_RATIONALE
            if source is ReviewSource.STRUCTURAL
            else "reviewer-flagged research gap"
        )
        review = SufficiencyReview(
            verdict=SufficiencyVerdict.RESEARCH_GAP,
            source=source,
            rationale=reason_text,
            gap_hint=hint,
        )
        if ctx.run.replan_count >= ctx.run.policy.max_replans:
            outcome = AnalysisSufficiencyOutcome(
                review=review, source_evidence_hash=ev_hash, analysis=analysis
            )
            return PhaseOutcome(
                disposition=AdvanceDisposition.PROGRESSED,
                reason="analyze-gap-exhausted",
                stage_logical_key="analyze",
                input_fingerprint=fp,
                proposal=outcome,
                termination_reason=TerminationReason.REQUIRED_EVIDENCE_MISSING,
            )
        try:
            focused = self._build_focused_replan(ctx, backend, hint)
        except Exception as exc:
            return self._idle(fp, f"analyze-replan-failed:{type(exc).__name__}")
        outcome = AnalysisSufficiencyOutcome(
            review=review,
            source_evidence_hash=ev_hash,
            analysis=analysis,
            focused_replan=focused,
        )
        return PhaseOutcome(
            disposition=AdvanceDisposition.PROGRESSED,
            next_state=None,
            reason="analyze-research-gap",
            stage_logical_key="analyze",
            input_fingerprint=fp,
            proposal=outcome,
            output_entities=(
                "verdict:research_gap",
                focused.gap.gap_id,
                focused.gap.focus_hash,
                f"source_evidence_hash:{ev_hash}",
            ),
            handled_accept=True,
            continue_immediately=True,
        )

    def _build_focused_replan(self, ctx, backend, gap_hint):
        approved = self._approved_research_capabilities(ctx, backend)
        return self.focused_replan_builder.build(
            run_id=ctx.run.run_id,
            objective=ctx.run.effective_goal,
            approved_research_capabilities=approved,
            gap_hint=gap_hint,
        )

    def _approved_research_capabilities(self, ctx, backend) -> tuple[CapabilityKind, ...]:
        with backend.unit_of_work() as uow:
            tasks = [
                t
                for t in uow.tasks.by_run(ctx.run.run_id, plan_version=ctx.run.current_plan_version)
                if t.worker_role is WorkerRole.EVIDENCE_RESEARCHER
            ]
            uow.commit()
        caps: list[CapabilityKind] = []
        for t in tasks:
            caps.extend(t.required_capabilities)
        return tuple(dict.fromkeys(caps))

    def _idle(self, fp, reason: str) -> PhaseOutcome:
        return PhaseOutcome(
            disposition=AdvanceDisposition.IDLE,
            reason=reason,
            failure_code=FailureCode.UPSTREAM_ERROR,
            stage_logical_key="analyze",
            input_fingerprint=fp,
            continue_immediately=False,
        )

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now: datetime) -> Run:
        suc: AnalysisSufficiencyOutcome = outcome.proposal  # type: ignore[assignment]
        if suc.is_gap and suc.focused_replan is not None:
            focused = suc.focused_replan
            correlation = {
                "gap_id": focused.gap.gap_id,
                "focus_hash": focused.gap.focus_hash,
                "source_phase": "analyze",
                "source_state_version": run.state_version,
                "source_evidence_hash": suc.source_evidence_hash,
            }
            _plan, new_run = ReplanService(
                uow,
                self.validator,
                PlanAcceptor(uow, self.clock, self.idgen),
                self.clock,
                self.idgen,
            ).replan_and_transition(
                run,
                focused.proposal,
                transition_to=RunState.RESEARCHING,
                correlation=correlation,
                now=now,
            )
            return new_run
        # sufficient/conflict: persist the transition to WRITING (the coordinator
        # records the accepted artifact stage via outcome.output_refs).
        return _simple_accept(outcome, run, uow, now)


class WritingPhaseHandler:
    """WRITING phase (tasks 7.4/7.5): drive ReportWriter Tasks, then produce an
    immutable Report Draft bound to the AnalysisArtifact before REVIEWING."""

    phase = PhaseId.WRITE

    def __init__(self, writer: ReportWriter, analysis_provider, *, clock, idgen) -> None:
        self.writer = writer
        self.analysis_provider = analysis_provider
        self.clock = clock
        self.idgen = idgen

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        fp = _fingerprint(ctx.run.state_version, ctx.run.current_plan_version)
        try:
            analysis = await self.analysis_provider(ctx.run.run_id)
            report = await self.writer(ctx.run.run_id, analysis)
        except Exception as exc:  # provider failure -> degrade
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason=f"write-provider-failed:{type(exc).__name__}",
                failure_code=FailureCode.UPSTREAM_ERROR,
                stage_logical_key="write",
                input_fingerprint=fp,
            )
        return PhaseOutcome(
            disposition=AdvanceDisposition.PROGRESSED,
            next_state=RunState.REVIEWING,
            reason="draft-written",
            stage_logical_key="write",
            input_fingerprint=fp,
            proposal=report,
        )

    accept = staticmethod(_simple_accept)


class ReviewPhaseHandler:
    """REVIEWING phase (tasks 7.6-7.9): drive Reviewer Tasks, then map the
    verdict through deterministic policy. Revision/Replan counters are monotonic
    and bounded by RunPolicy; exhaustion degrades instead of looping forever."""

    phase = PhaseId.REVIEW

    def __init__(self, reviewer: ReportReviewer, report_provider, *, clock, idgen) -> None:
        self.reviewer = reviewer
        self.report_provider = report_provider
        self.clock = clock
        self.idgen = idgen

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        fp = _fingerprint(ctx.run.state_version, ctx.run.current_plan_version)
        try:
            report = await self.report_provider(ctx.run.run_id)
            proposal = await self.reviewer(ctx.run.run_id, report)
        except Exception as exc:  # provider failure -> degrade
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason=f"review-provider-failed:{type(exc).__name__}",
                failure_code=FailureCode.UPSTREAM_ERROR,
                stage_logical_key="review",
                input_fingerprint=fp,
            )
        return self._map_verdict(ctx, proposal, fp)

    def _map_verdict(self, ctx: PhaseContext, proposal: ReviewProposal, fp) -> PhaseOutcome:
        verdict = proposal.verdict
        if verdict is ReviewVerdict.PASS:  # -> FINALIZING (7.7)
            return PhaseOutcome(
                AdvanceDisposition.PROGRESSED,
                next_state=RunState.FINALIZING,
                reason="review-pass",
                stage_logical_key="review",
                input_fingerprint=fp,
                proposal=proposal,
            )
        if verdict is ReviewVerdict.REVISE:  # -> WRITING if revision budget remains (7.8)
            if ctx.run.revision_count >= ctx.run.policy.max_report_revisions:
                return PhaseOutcome(
                    AdvanceDisposition.IDLE,
                    reason="revision-exhausted",
                    failure_code=FailureCode.POLICY_VIOLATION,
                    stage_logical_key="review",
                    input_fingerprint=fp,
                    proposal=proposal,
                )
            return PhaseOutcome(
                AdvanceDisposition.PROGRESSED,
                next_state=RunState.WRITING,
                reason="review-revise",
                stage_logical_key="review",
                input_fingerprint=fp,
                proposal=proposal,
                bump_revision=True,
            )
        if verdict is ReviewVerdict.RESEARCH_GAP:
            # analyze-sufficiency-feedback 7.1: opening the Gate does NOT consume
            # replan budget — the shared Focused Replan runs on continuation
            # (resolved) and re-checks max_replans there (7.3).
            if ctx.run.replan_count >= ctx.run.policy.max_replans:
                return PhaseOutcome(
                    AdvanceDisposition.IDLE,
                    reason="replan-exhausted",
                    failure_code=FailureCode.POLICY_VIOLATION,
                    stage_logical_key="review",
                    input_fingerprint=fp,
                    proposal=proposal,
                )
            feedback = sanitize_gap(
                " ".join(
                    part
                    for part in (proposal.reason, *proposal.suggested_actions)
                    if part and part.strip()
                )
                or "Report review identified missing research evidence."
            )
            return PhaseOutcome(
                AdvanceDisposition.BLOCKED,
                next_state=None,
                reason="review-research-gap",
                open_gate=GateType.CONFLICT_RESOLUTION,
                gate_intent=GateContinuationIntent.REVIEW_RESEARCH_GAP,
                gate_feedback=feedback.cleaned,
                gate_correlation_id=feedback.gap_id,
                stage_logical_key="review",
                input_fingerprint=fp,
                proposal=proposal,
            )
        # CONFLICT / ESCALATE -> conflict Gate (7.7)
        return PhaseOutcome(
            AdvanceDisposition.BLOCKED,
            next_state=None,
            reason=f"review-{verdict.value}",
            open_gate=GateType.CONFLICT_RESOLUTION,
            stage_logical_key="review",
            input_fingerprint=fp,
            proposal=proposal,
        )

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now) -> Run:
        updates: dict[str, object] = {}
        if outcome.next_state is not None and outcome.next_state is not run.state:
            assert_run_transition(run.state, outcome.next_state)
            updates["state"] = outcome.next_state
        # Monotonic counters — never reset across phase transitions (task 7.8).
        # Structured flags (not reason-string matching) drive the bumps.
        if outcome.bump_revision:
            updates["revision_count"] = run.revision_count + 1
        if outcome.bump_replan:
            updates["replan_count"] = run.replan_count + 1
        if not updates:
            return run
        updates.update({"updated_at": now, "state_version": run.state_version + 1})
        moved = run.model_copy(update=updates)
        uow.runs.save(moved, expected_version=run.state_version)
        return moved


class FinalizePhaseHandler:
    """FINALIZING phase (tasks 7.10-7.13): deterministically evaluate the
    Completion Contract, build immutable report.md/json/run-summary.json, and
    freeze the terminal state with artifact hashes bound to the transition."""

    phase = PhaseId.FINALIZE

    def __init__(
        self,
        *,
        report_provider,
        analysis_provider,
        evidence_provider,
        deliverables_provider,
        clock,
        idgen,
    ) -> None:
        self.report_provider = report_provider
        self.analysis_provider = analysis_provider
        self.evidence_provider = evidence_provider
        self.deliverables_provider = deliverables_provider
        self.clock = clock
        self.idgen = idgen

    async def execute(self, ctx: PhaseContext, backend) -> PhaseOutcome:
        fp = _fingerprint(ctx.run.state_version, ctx.run.current_plan_version)
        try:
            report = await self.report_provider(ctx.run.run_id)
            analysis = await self.analysis_provider(ctx.run.run_id)
            evidence = await self.evidence_provider(ctx.run.run_id)
            deliverables = await self.deliverables_provider(ctx.run.run_id)
        except Exception as exc:  # provider failure -> degrade (task 7.10)
            return PhaseOutcome(
                disposition=AdvanceDisposition.IDLE,
                reason=f"finalize-provider-failed:{type(exc).__name__}",
                failure_code=FailureCode.UPSTREAM_ERROR,
                stage_logical_key="finalize",
                input_fingerprint=fp,
            )
        return PhaseOutcome(
            disposition=AdvanceDisposition.PROGRESSED,
            next_state=None,
            reason="finalize",
            stage_logical_key="finalize",
            input_fingerprint=fp,
            proposal=(report, analysis, evidence, deliverables),
        )

    def accept(self, outcome: PhaseOutcome, run: Run, uow, now) -> Run:
        report, analysis, evidence, deliverables = outcome.proposal
        contract = uow.completion.get(run.run_id)
        if contract is None:
            raise RuntimeError(f"completion contract missing for run {run.run_id} at finalize")
        _per, overall = CompletionEvaluator().evaluate(
            contract,
            evidence=evidence,
            deliverables_present=deliverables,
        )
        artifacts = ReportBuilder().build(
            uow.artifacts,
            run_id=run.run_id,
            report=report,
            analysis=analysis,
            completion_overall=overall,
            evidence=evidence,
            degradations=(),
            termination=run.termination,
            clock=self.clock,
        )
        terminal, _reason = Finalizer().finalize(
            uow,
            run,
            artifacts=artifacts,
            completion_overall=overall,
            clock=self.clock,
            idgen=self.idgen,
        )
        return terminal


__all__ = [
    "AnalysisPhaseHandler",
    "Analyst",
    "FinalizePhaseHandler",
    "GoalPhaseHandler",
    "PlanningPhaseHandler",
    "ReportReviewer",
    "ReportWriter",
    "ResearchPhaseHandler",
    "ReviewPhaseHandler",
    "WritingPhaseHandler",
]

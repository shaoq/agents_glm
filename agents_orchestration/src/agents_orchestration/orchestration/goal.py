"""Goal ambiguity detection and Completion Contract amendment (tasks 5.3 / 5.8).

The structural ambiguity check is deterministic; deeper material-ambiguity
detection is delegated to the (injected, model-backed) GoalNormalizer. Either
path may surface a GOAL_CLARIFICATION gate Proposal, but only the deterministic
control plane opens the formal gate (Section 9).
"""

from __future__ import annotations

from agents_orchestration.domain.enums import EffectType
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run
from agents_orchestration.domain.goal import CompletionContract, CompletionCriterion, GoalSpec
from agents_orchestration.orchestration.proposals import GoalClarificationProposal


class GoalService:
    def __init__(self, uow, clock, idgen) -> None:
        self.uow = uow
        self.clock = clock
        self.idgen = idgen

    def detect_ambiguity(self, goal: GoalSpec, run_id: str) -> GoalClarificationProposal | None:
        """Return a clarification Proposal if the goal is materially ambiguous."""

        if not goal.is_materially_ambiguous:
            return None
        return GoalClarificationProposal(
            run_id=run_id,
            ambiguities=("missing objective or deliverables",),
            questions=("Clarify the research objective and required deliverables.",),
        )

    def amend_completion(
        self,
        run: Run,
        contract: CompletionContract,
        *,
        actor: str,
        reason: str,
        new_criteria: tuple[CompletionCriterion, ...],
        deliverable_paths: tuple[str, ...] | None = None,
    ) -> tuple[CompletionContract, Run]:
        """Versioned Completion Contract amendment (task 5.8)."""

        now = self.clock.now()
        amended = contract.amend(
            actor=actor,
            reason=reason,
            new_criteria=new_criteria,
            deliverable_paths=deliverable_paths,
        )
        self.uow.completion.save(run.run_id, amended)
        new_run = run.model_copy(update={"updated_at": now, "state_version": run.state_version + 1})
        self.uow.runs.save(new_run, expected_version=run.state_version)
        self.uow.events.append(
            [
                DomainEvent(
                    event_id=self.idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=EffectType.COMPLETION_AMENDED,
                    state_version=new_run.state_version,
                    occurred_at=now,
                    payload={"version": amended.version, "actor": actor, "reason": reason},
                )
            ]
        )
        return amended, new_run

"""Deterministic safety policy for model-proposed research-loop actions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import CapabilityKind, FailureCode
from agents_orchestration.domain.evidence import Evidence, Usage
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.plan import SeedExplorationBoundary
from agents_orchestration.domain.research_loop import (
    AddDirectionAction,
    EvidenceDigest,
    QueryAction,
    ResearchActionEnvelope,
    ResearchAgent,
    ResearchDirection,
    ResearchDirectionView,
    ResearchLoop,
    ResearchLoopView,
    ResearchStep,
    StopRequestAction,
)
from agents_orchestration.domain.worker import WorkerDefinition
from agents_orchestration.orchestration.sufficiency import GAP_HINT_MAX_LEN

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RUN = re.compile(r"\s+")
_UNTRUSTED_DIRECTION_LABEL = "[UNTRUSTED_DIRECTION]"


@dataclass(frozen=True)
class PreparedDirection:
    text: str
    cleaned: str
    focus_hash: str
    capability_scope: tuple[CapabilityKind, ...]


class ResearchDirectionPolicy:
    """Shared, side-effect-free sanitizer/hash/capability narrowing policy."""

    def __init__(self, allowed_capabilities: frozenset[CapabilityKind]) -> None:
        self.allowed_capabilities = frozenset(allowed_capabilities)

    @staticmethod
    def sanitize(raw: str, *, max_length: int = GAP_HINT_MAX_LEN) -> str:
        if raw is None:
            raise ValueError("direction text is required")
        no_control = _CONTROL_CHARS.sub(" ", raw)
        collapsed = _WHITESPACE_RUN.sub(" ", no_control).strip()
        if not collapsed:
            raise ValueError("direction text is empty after sanitization")
        cleaned = collapsed[:max_length].strip()
        if not cleaned:
            raise ValueError("direction text is empty after length cap")
        return cleaned

    @staticmethod
    def focus_hash(cleaned: str) -> str:
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
        return f"focus:{digest}"

    def narrow(
        self, approved_capabilities: tuple[CapabilityKind, ...]
    ) -> tuple[CapabilityKind, ...]:
        return tuple(
            dict.fromkeys(
                capability
                for capability in approved_capabilities
                if capability in self.allowed_capabilities
            )
        )

    def prepare(
        self,
        raw: str,
        *,
        approved_capabilities: tuple[CapabilityKind, ...],
    ) -> PreparedDirection:
        cleaned = self.sanitize(raw)
        return PreparedDirection(
            text=f"{_UNTRUSTED_DIRECTION_LABEL} {cleaned}",
            cleaned=cleaned,
            focus_hash=self.focus_hash(cleaned),
            capability_scope=self.narrow(approved_capabilities),
        )


class ActionValidationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    loop: ResearchLoop
    step: ResearchStep
    seed_boundary: SeedExplorationBoundary
    allowed_capabilities: frozenset[CapabilityKind]
    directions: tuple[ResearchDirection, ...]
    proposed_focus_hash: str | None = None


class ActionValidationError(ValueError):
    def __init__(self, failure_code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class ActionValidator:
    """Validate formal identity and boundary without interpreting prompt text."""

    def validate(
        self, envelope: ResearchActionEnvelope, context: ActionValidationContext
    ) -> ResearchActionEnvelope:
        loop = context.loop
        step = context.step
        identity = (
            envelope.run_id,
            envelope.plan_version,
            envelope.task_id,
            envelope.loop_id,
            envelope.step_id,
        )
        expected = (
            loop.run_id,
            loop.plan_version,
            loop.task_id,
            loop.loop_id,
            step.step_id,
        )
        if identity != expected or step.loop_id != loop.loop_id:
            raise ActionValidationError(
                FailureCode.INVALID_RESPONSE, "action identity does not match current step"
            )
        if step.step_index != loop.next_step_index:
            raise ActionValidationError(
                FailureCode.INVALID_RESPONSE, "action references a non-current logical step"
            )
        if loop.step_count >= context.seed_boundary.max_steps:
            raise ActionValidationError(FailureCode.BUDGET_EXCEEDED, "loop max_steps exhausted")

        directions = {direction.direction_id: direction for direction in context.directions}
        action = envelope.action
        if isinstance(action, QueryAction):
            direction = directions.get(action.direction_id)
            if direction is None:
                raise ActionValidationError(
                    FailureCode.INVALID_RESPONSE, "query references unknown direction"
                )
            if (
                action.capability_kind not in context.allowed_capabilities
                or action.capability_kind not in direction.capability_scope
            ):
                raise ActionValidationError(
                    FailureCode.POLICY_VIOLATION,
                    "query capability is outside the approved boundary",
                )
        elif isinstance(action, AddDirectionAction):
            if action.parent_direction_id not in directions:
                raise ActionValidationError(
                    FailureCode.INVALID_RESPONSE, "new direction references unknown parent"
                )
            duplicate = context.proposed_focus_hash is not None and any(
                direction.focus_hash == context.proposed_focus_hash
                for direction in context.directions
            )
            if loop.direction_count >= context.seed_boundary.max_directions and not duplicate:
                raise ActionValidationError(
                    FailureCode.BUDGET_EXCEEDED, "loop max_directions exhausted"
                )
        elif isinstance(action, StopRequestAction):
            # Full coverage/ownership validation is centralized in LoopGuard.
            pass
        return envelope


@dataclass(frozen=True)
class LoopGuardResult:
    accepted: bool
    reasons: tuple[str, ...] = ()


class LoopGuard:
    """Structural STOP guard; semantic sufficiency remains in ANALYZE."""

    def evaluate(
        self,
        loop: ResearchLoop,
        boundary: SeedExplorationBoundary,
        stop: StopRequestAction,
        *,
        other_in_flight_steps: int = 0,
        accepted_step_count: int | None = None,
        persisted_direction_count: int | None = None,
    ) -> LoopGuardResult:
        reasons: list[str] = []
        if not set(boundary.required_coverage).issubset(loop.coverage):
            reasons.append("required_coverage_missing")
        owned = set(loop.accepted_evidence_ids)
        supporting = set(stop.supporting_evidence_ids)
        if not supporting.issubset(owned):
            reasons.append("supporting_evidence_not_owned")
        if not owned:
            reasons.append("independent_evidence_missing")
        if other_in_flight_steps:
            reasons.append("step_in_flight")
        if (
            loop.step_count != loop.next_step_index
            or accepted_step_count is not None
            and loop.step_count != accepted_step_count
        ):
            reasons.append("step_counter_inconsistent")
        if (
            persisted_direction_count is not None
            and loop.direction_count != persisted_direction_count
        ):
            reasons.append("direction_counter_inconsistent")
        return LoopGuardResult(not reasons, tuple(reasons))


@dataclass(frozen=True)
class ResearchDecisionOutcome:
    envelope: ResearchActionEnvelope
    usage: Usage


class ResearchAgentLoopExecutor:
    """Runs model decision and at most one routed capability operation.

    It has no repository access. ``RuntimeTick`` owns all durable
    DECIDING/PREPARED/ACCEPT transitions and invokes these methods only outside
    write transactions.
    """

    def __init__(
        self,
        *,
        agent: ResearchAgent,
        validator: ActionValidator,
        direction_policy: ResearchDirectionPolicy,
        registry,
        router,
        worker: WorkerDefinition,
    ) -> None:
        if worker is None:
            raise ValueError("evidence researcher worker definition is required")
        self.agent = agent
        self.validator = validator
        self.direction_policy = direction_policy
        self.registry = registry
        self.router = router
        self.worker = worker

    async def decide(
        self,
        *,
        run: Run,
        task: Task,
        loop: ResearchLoop,
        step: ResearchStep,
        seed_boundary: SeedExplorationBoundary,
        allowed_capabilities: frozenset[CapabilityKind],
        directions: tuple[ResearchDirection, ...],
        evidence: tuple[Evidence, ...],
    ) -> ResearchDecisionOutcome:
        view = self._build_view(
            run=run,
            task=task,
            loop=loop,
            step=step,
            seed_boundary=seed_boundary,
            directions=directions,
            evidence=evidence,
        )
        decision = await self.agent.decide(view, decision_request_id=step.decision_request_id)
        envelope = ResearchActionEnvelope(
            run_id=run.run_id,
            plan_version=step.plan_version,
            task_id=task.task_id,
            loop_id=loop.loop_id,
            step_id=step.step_id,
            action=decision.action,
        )
        proposed_focus_hash = None
        if isinstance(decision.action, AddDirectionAction):
            proposed_focus_hash = self.direction_policy.prepare(
                decision.action.hint,
                approved_capabilities=tuple(allowed_capabilities),
            ).focus_hash
        self.validator.validate(
            envelope,
            ActionValidationContext(
                loop=loop,
                step=step,
                seed_boundary=seed_boundary,
                allowed_capabilities=allowed_capabilities,
                directions=directions,
                proposed_focus_hash=proposed_focus_hash,
            ),
        )
        return ResearchDecisionOutcome(envelope=envelope, usage=decision.usage)

    async def execute_action(
        self,
        *,
        decision: ResearchDecisionOutcome,
        step: ResearchStep,
        task: Task,
        attempt: Attempt,
        run: Run,
    ) -> CapabilityResult | None:
        action = decision.envelope.action
        if not isinstance(action, QueryAction):
            return None
        descriptor = self.registry.find_kind(action.capability_kind)
        if descriptor is None:
            return CapabilityResult.failed(
                operation_id=f"operation:{step.step_id}",
                failure_code=FailureCode.UNAVAILABLE,
                retryable=True,
            )
        request = CapabilityRequest(
            request_id=step.capability_request_id,
            capability_id=descriptor.capability_id,
            worker_id=self.worker.worker_id,
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            inputs={"query": action.query},
            deadline_at=run.budget.deadline_at,
        )
        return await self.router.invoke(request, worker=self.worker, run_policy=run.policy)

    @staticmethod
    def _build_view(
        *,
        run: Run,
        task: Task,
        loop: ResearchLoop,
        step: ResearchStep,
        seed_boundary: SeedExplorationBoundary,
        directions: tuple[ResearchDirection, ...],
        evidence: tuple[Evidence, ...],
    ) -> ResearchLoopView:
        owned = set(loop.accepted_evidence_ids)
        seen_sources: set[tuple[str, str]] = set()
        digests: list[EvidenceDigest] = []
        for item in evidence:
            if item.evidence_id not in owned or item.dedup_key in seen_sources:
                continue
            seen_sources.add(item.dedup_key)
            content = (item.content_text or item.content_ref or "(no inline content)")[:1_800]
            digests.append(
                EvidenceDigest(
                    evidence_id=item.evidence_id,
                    source_kind=item.source.source_kind.value,
                    digest=(
                        f"[UNTRUSTED_EVIDENCE source={item.source.source_kind.value}] {content}"
                    ),
                    is_untrusted=True,
                )
            )
            if len(digests) >= 100:
                break

        remaining_tokens = (
            None
            if seed_boundary.max_tokens is None
            else max(0, seed_boundary.max_tokens - loop.usage.tokens)
        )
        remaining_cost = (
            None
            if seed_boundary.max_cost_usd is None
            else max(0, seed_boundary.max_cost_usd - loop.usage.cost_usd)
        )
        objective = run.goal.objective if run.goal is not None else run.raw_goal
        return ResearchLoopView(
            run_id=run.run_id,
            plan_version=loop.plan_version,
            task_id=task.task_id,
            loop_id=loop.loop_id,
            step_id=step.step_id,
            objective=objective[:4_000],
            seed=(task.description or task.task_id)[:2_000],
            directions=tuple(
                ResearchDirectionView(
                    direction_id=direction.direction_id,
                    parent_direction_id=direction.parent_direction_id,
                    text=direction.text[:1_000],
                    capability_scope=direction.capability_scope,
                )
                for direction in directions[-50:]
            ),
            evidence=tuple(digests),
            coverage=loop.coverage,
            remaining_steps=max(0, seed_boundary.max_steps - loop.step_count),
            remaining_directions=max(0, seed_boundary.max_directions - loop.direction_count),
            remaining_tokens=remaining_tokens,
            remaining_cost_usd=remaining_cost,
        )


__all__ = [
    "ActionValidationContext",
    "ActionValidationError",
    "ActionValidator",
    "LoopGuard",
    "LoopGuardResult",
    "PreparedDirection",
    "ResearchDirectionPolicy",
    "ResearchAgentLoopExecutor",
    "ResearchDecisionOutcome",
]

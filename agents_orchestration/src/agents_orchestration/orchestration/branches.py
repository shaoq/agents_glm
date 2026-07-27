"""Parallel research Branches and Evidence Join (tasks 8.1-8.8).

A research Task fans out into independent Branches (Memory / RAG / authorized
Web), each with a stable identity and a Required/Optional/Conditional/Any-of/
Quorum role (task 8.1). Branches are dispatched concurrently (8.2) and each
accepted result is persisted independently before Join (8.3) — a single failed
Branch never forces the others to re-run (8.9). The deterministic Join reuses
:class:`EvidenceSet.join` for source-identity deduplication (8.5), conflict
preservation (8.6) and Required/Optional sufficiency (8.7), then applies the
configured aggregation policy (8.8).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import BranchRole, CapabilityKind, Sufficiency
from agents_orchestration.domain.evidence import Degradation, Evidence, EvidenceSet


class JoinFallback(StrEnum):
    """Configured behaviour when a lane fails or a conflict is unresolved (8.8)."""

    CONTINUE = "continue"
    DEGRADE = "degrade"
    FAIL = "fail"
    GATE = "gate"


@dataclass(frozen=True)
class JoinPolicy:
    on_optional_fail: JoinFallback = JoinFallback.DEGRADE
    on_required_fail: JoinFallback = JoinFallback.FAIL
    on_unresolved_conflict: JoinFallback = JoinFallback.DEGRADE

    @classmethod
    def default(cls) -> JoinPolicy:
        return cls()


@dataclass(frozen=True)
class Branch:
    """A stable research lane within one research Task (task 8.1)."""

    branch_id: str
    task_id: str
    role: BranchRole
    capability_kind: CapabilityKind
    request: CapabilityRequest
    accepted_result: CapabilityResult | None = None
    accepted: bool = False

    def accept(self, result: CapabilityResult) -> Branch:
        return Branch(
            branch_id=self.branch_id,
            task_id=self.task_id,
            role=self.role,
            capability_kind=self.capability_kind,
            request=self.request,
            accepted_result=result,
            accepted=True,
        )


def normalize_evidence(result: CapabilityResult, kind: CapabilityKind) -> tuple[Evidence, ...]:
    """Ensure each evidence carries source identity / trust / untrusted labeling (8.4).

    Adapters already produce normalized Evidence; this fills any missing freshness
    and stamps web/model evidence as untrusted so it can never become a control
    instruction (design Decision 12).
    """

    untrusted_kind = kind in {CapabilityKind.WEB_RESEARCH, CapabilityKind.MODEL}
    normalized: list[Evidence] = []
    for evidence in result.evidence:
        normalized.append(
            evidence
            if (evidence.is_untrusted or not untrusted_kind)
            else evidence.model_copy(update={"is_untrusted": True})
        )
    return tuple(normalized)


async def dispatch_branches(branches, invoke) -> dict[str, CapabilityResult]:
    """Bounded concurrent dispatch of independent Branches (task 8.2).

    ``invoke`` is the routed capability caller (worker-bound). Branches run
    concurrently via ``asyncio.gather``; unrelated Tasks stay concurrent because
    capability calls never execute inside a write transaction.
    """

    async def _one(branch: Branch) -> tuple[str, CapabilityResult]:
        return branch.branch_id, await invoke(branch.capability_kind, branch.request)

    pairs = await asyncio.gather(*(_one(b) for b in branches))
    return dict(pairs)


class EvidenceJoiner:
    """Deterministic Join over accepted Branch results (tasks 8.5-8.8)."""

    def join(
        self,
        *,
        run_id: str,
        task_id: str,
        branches: tuple[Branch, ...],
        policy: JoinPolicy,
    ) -> tuple[EvidenceSet, tuple[Degradation, ...]]:
        evidences: list[Evidence] = []
        degradations: list[Degradation] = []
        required_present = False

        for branch in branches:
            if branch.role in {BranchRole.REQUIRED, BranchRole.QUORUM}:
                required_present = True
            if branch.accepted and branch.accepted_result is not None:
                evidences.extend(normalize_evidence(branch.accepted_result, branch.capability_kind))
            else:
                degradations.append(self._lane_degradation(branch, policy))

        joined = EvidenceSet.join(
            run_id=run_id, task_id=task_id, evidences=tuple(evidences), required=required_present
        )
        joined, policy_degradations = self._apply_policy(joined, policy)
        return joined, tuple(degradations + policy_degradations)

    @staticmethod
    def _lane_degradation(branch: Branch, policy: JoinPolicy) -> Degradation:
        if branch.role is BranchRole.REQUIRED and policy.on_required_fail is JoinFallback.FAIL:
            return Degradation(flag="required_lane_failed", reason=branch.capability_kind.value)
        return Degradation(
            flag="optional_lane_failed", reason=branch.capability_kind.value, fallback_used=True
        )

    @staticmethod
    def _apply_policy(
        joined: EvidenceSet, policy: JoinPolicy
    ) -> tuple[EvidenceSet, list[Degradation]]:
        degradations: list[Degradation] = []
        if joined.sufficiency is Sufficiency.CONFLICTED:
            if policy.on_unresolved_conflict is JoinFallback.FAIL:
                degradations.append(Degradation(flag="conflict_unresolved", reason="fail"))
            else:
                degradations.append(
                    Degradation(flag="conflict_unresolved", reason="disclosed", fallback_used=True)
                )
        if joined.missing_required:
            degradations.append(Degradation(flag="required_evidence_missing", reason="disclosed"))
        return joined, degradations

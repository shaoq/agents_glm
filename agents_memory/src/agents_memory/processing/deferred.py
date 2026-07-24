"""Build and merge deferred event assertions without persistence side effects."""

from datetime import UTC, datetime
from uuid import uuid4

from agents_memory.models import (
    Action,
    ActionPlan,
    MemoryScope,
    Message,
    PendingResolution,
    PendingResolutionStatus,
    RelationKind,
)
from agents_memory.processing.event_matching import group_frames_related
from agents_memory.processing.pending import (
    PendingResolutionPolicy,
    missing_event_dimensions,
)


class DeferredResolutionCollector:
    """Maintain the pending groups produced while planning one write batch."""

    def __init__(self, policy: PendingResolutionPolicy) -> None:
        self.policy = policy

    def collect(
        self,
        groups: list[PendingResolution],
        *,
        scope: MemoryScope,
        plan: ActionPlan,
        messages: list[Message],
        now: datetime | None = None,
    ) -> PendingResolution:
        """Add one DEFER plan to its related group and return the updated item."""

        if plan.action is not Action.DEFER:
            raise ValueError("deferred collector requires a DEFER plan")
        current = now or datetime.now(UTC)
        candidate = plan.candidate
        existing = next(
            (
                group
                for group in groups
                if set(group.conflicting_memory_ids) & set(plan.target_ids)
                or group_frames_related(
                    group.grouped_candidates or (group.candidate,),
                    candidate,
                )
            ),
            None,
        )
        if existing is None:
            pending = self._new_pending(scope, plan, messages, current)
            groups.append(pending)
            return pending

        pending = self._merge_pending(existing, plan, messages, current)
        groups[groups.index(existing)] = pending
        return pending

    def _new_pending(
        self,
        scope: MemoryScope,
        plan: ActionPlan,
        messages: list[Message],
        now: datetime,
    ) -> PendingResolution:
        candidate = plan.candidate
        return PendingResolution(
            id=str(uuid4()),
            scope=scope,
            candidate=candidate,
            grouped_candidates=(candidate,),
            conflicting_memory_ids=plan.target_ids,
            semantic_relation=self._semantic_relation(plan),
            missing_dimensions=missing_event_dimensions(candidate),
            reason=plan.reason,
            source_message_ids=candidate.source_message_ids,
            source_messages=tuple(
                message
                for message in messages
                if message.message_id in candidate.source_message_ids
            ),
            processed_evidence_message_ids=candidate.source_message_ids,
            importance=candidate.importance,
            status=PendingResolutionStatus.OPEN,
            created_at=now,
            updated_at=now,
            last_evaluated_at=now,
            expires_at=self.policy.expires_at(candidate.importance, now=now),
        )

    @staticmethod
    def _merge_pending(
        existing: PendingResolution,
        plan: ActionPlan,
        messages: list[Message],
        now: datetime,
    ) -> PendingResolution:
        candidate = plan.candidate
        source_ids = tuple(
            dict.fromkeys((*existing.source_message_ids, *candidate.source_message_ids))
        )
        return existing.model_copy(
            update={
                "grouped_candidates": (*existing.grouped_candidates, candidate),
                "conflicting_memory_ids": tuple(
                    dict.fromkeys((*existing.conflicting_memory_ids, *plan.target_ids))
                ),
                "source_message_ids": source_ids,
                "source_messages": tuple(
                    {
                        message.message_id: message
                        for message in (*existing.source_messages, *messages)
                        if message.message_id in source_ids
                    }.values()
                ),
                "processed_evidence_message_ids": tuple(
                    dict.fromkeys(
                        (
                            *existing.processed_evidence_message_ids,
                            *candidate.source_message_ids,
                        )
                    )
                ),
                "importance": max(existing.importance, candidate.importance),
                "updated_at": now,
            }
        )

    @staticmethod
    def _semantic_relation(plan: ActionPlan) -> RelationKind:
        if plan.relation is not None:
            return plan.relation
        return next(
            (match.relation for match in plan.matches if match.relation is not RelationKind.NONE),
            plan.matches[0].relation,
        )

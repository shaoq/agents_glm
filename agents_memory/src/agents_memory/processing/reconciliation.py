from dataclasses import dataclass
from datetime import UTC, datetime

from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    EventFrame,
    MemoryScope,
    MemoryType,
    Message,
    PendingResolution,
    PendingResolutionStatus,
    SourceKind,
    TemporalResolution,
    Validity,
)
from agents_memory.processing.decision import DecisionEngine
from agents_memory.processing.pending import missing_event_dimensions
from agents_memory.resolution.base import RelationResolver
from agents_memory.storage.repository import MemoryRepository


@dataclass(frozen=True)
class ReconciliationResult:
    plans: tuple[ActionPlan, ...] = ()
    consumed_candidate_indexes: frozenset[int] = frozenset()


class PendingResolutionReconciler:
    def __init__(
        self,
        repository: MemoryRepository,
        decision_engine: DecisionEngine,
        *,
        max_pending: int = 20,
    ) -> None:
        self.repository = repository
        self.decision_engine = decision_engine
        self.max_pending = max_pending

    def reconcile(
        self,
        *,
        scope: MemoryScope,
        messages: list[Message],
        candidates: list[CandidateMemory],
        resolver: RelationResolver,
    ) -> ReconciliationResult:
        event_candidates = [
            (index, candidate)
            for index, candidate in enumerate(candidates)
            if candidate.type is MemoryType.EVENT
        ]
        consumed: set[int] = set()
        plans: list[ActionPlan] = []
        claimed_targets: set[str] = set()
        now = datetime.now(UTC)
        for pending in self.repository.list_pending_resolutions(scope)[
            : self.max_pending
        ]:
            self._validate_message_ids(pending.source_messages, tuple(messages))
            new_messages = [
                message
                for message in messages
                if message.message_id
                not in pending.processed_evidence_message_ids
            ]
            if not new_messages:
                continue
            synthetic_index = len(candidates) + len(plans)
            if pending.expires_at <= now:
                updated = pending.model_copy(
                    update={
                        "status": PendingResolutionStatus.EXPIRED,
                        "updated_at": now,
                        "last_evaluated_at": now,
                    }
                )
                plans.append(
                    ActionPlan(
                        candidate_index=synthetic_index,
                        candidate=pending.candidate,
                        action=Action.NOOP,
                        pending_resolution=updated,
                        reason="pending resolution expired",
                    )
                )
                continue
            if claimed_targets & set(pending.conflicting_memory_ids):
                updated = pending.model_copy(
                    update={
                        "status": PendingResolutionStatus.OBSOLETE,
                        "updated_at": now,
                        "last_evaluated_at": now,
                        "reason": "target superseded by another resolution",
                    }
                )
                plans.append(
                    ActionPlan(
                        candidate_index=synthetic_index,
                        candidate=pending.candidate,
                        action=Action.NOOP,
                        pending_resolution=updated,
                        reason=updated.reason,
                    )
                )
                continue
            evidence = next(
                (
                    (index, candidate)
                    for index, candidate in event_candidates
                    if index not in consumed
                    and self._frames_related(
                        pending.candidate.event_frame, candidate.event_frame
                    )
                ),
                None,
            )
            if evidence is None:
                index = synthetic_index
                relevant_messages = [
                    message
                    for message in new_messages
                    if self._eligible_role(
                        pending.candidate.source_kind, message
                    )
                    and self._message_related(pending, message)
                ]
                if not relevant_messages:
                    continue
                evidence_candidate = None
            else:
                index, evidence_candidate = evidence
                consumed.add(index)
                evidence_ids = set(evidence_candidate.source_message_ids)
                relevant_messages = [
                    message
                    for message in new_messages
                    if message.message_id in evidence_ids
                    and self._eligible_role(
                        evidence_candidate.source_kind, message
                    )
                ]
                if not relevant_messages:
                    consumed.remove(index)
                    continue

            candidate, resolver_candidate = self._compose_assertion(
                pending, evidence_candidate, relevant_messages
            )

            targets = self.repository.get_memories(
                list(pending.conflicting_memory_ids)
            )
            active_targets = [
                target for target in targets if target.validity is Validity.ACTIVE
            ]
            merged_messages = self._merge_messages(
                pending.source_messages, tuple(relevant_messages)
            )
            used_ids = tuple(
                message.message_id for message in relevant_messages
            )
            processed = tuple(
                dict.fromkeys(
                    (*pending.processed_evidence_message_ids, *used_ids)
                )
            )
            if not active_targets:
                updated = pending.model_copy(
                    update={
                        "status": PendingResolutionStatus.OBSOLETE,
                        "updated_at": now,
                        "last_evaluated_at": now,
                        "processed_evidence_message_ids": processed,
                        "source_messages": merged_messages,
                    }
                )
                plans.append(
                    ActionPlan(
                        candidate_index=index,
                        candidate=candidate,
                        action=Action.NOOP,
                        pending_resolution=updated,
                        reason="deferred target is no longer active",
                    )
                )
                continue

            relations = resolver.resolve(resolver_candidate, active_targets)
            plan = self.decision_engine.decide(
                index, candidate, active_targets, relations
            )
            if plan.action is Action.UPDATE:
                claimed_targets.update(plan.target_ids)
            status = (
                PendingResolutionStatus.OPEN
                if plan.action is Action.DEFER
                else PendingResolutionStatus.RESOLVED
            )
            updated = pending.model_copy(
                update={
                    "candidate": candidate,
                    "grouped_candidates": (candidate,),
                    "status": status,
                    "updated_at": now,
                    "last_evaluated_at": now,
                    "processed_evidence_message_ids": processed,
                    "source_message_ids": candidate.source_message_ids,
                    "source_messages": merged_messages,
                    "missing_dimensions": missing_event_dimensions(candidate),
                    "reason": plan.reason,
                }
            )
            plans.append(
                plan.model_copy(update={"pending_resolution": updated})
            )
        return ReconciliationResult(tuple(plans), frozenset(consumed))

    @classmethod
    def _compose_assertion(
        cls,
        pending: PendingResolution,
        evidence: CandidateMemory | None,
        messages: list[Message],
    ) -> tuple[CandidateMemory, CandidateMemory]:
        grouped = pending.grouped_candidates or (pending.candidate,)
        contents = tuple(dict.fromkeys(item.content for item in grouped))
        frame = pending.candidate.event_frame
        for grouped_candidate in (
            *grouped[1:],
            *((evidence,) if evidence is not None else ()),
        ):
            if frame is None:
                frame = grouped_candidate.event_frame
                continue
            if grouped_candidate.event_frame is None:
                continue
            incoming = grouped_candidate.event_frame
            updates = {
                field: getattr(incoming, field)
                for field in (
                    "actor",
                    "predicate",
                    "object",
                    "location",
                    "polarity",
                    "modality",
                )
                if getattr(frame, field) == "unknown"
                and getattr(incoming, field) != "unknown"
            }
            if (
                frame.status.value == "unknown"
                and incoming.status.value != "unknown"
            ):
                updates["status"] = incoming.status
            if (
                frame.temporal_anchor.resolution
                is TemporalResolution.UNRESOLVED
                and incoming.temporal_anchor.resolution
                is not TemporalResolution.UNRESOLVED
            ):
                updates["temporal_anchor"] = incoming.temporal_anchor
            frame = frame.model_copy(update=updates)
        source_ids = tuple(
            dict.fromkeys(
                (
                    *pending.source_message_ids,
                    *(evidence.source_message_ids if evidence else ()),
                    *(message.message_id for message in messages),
                )
            )
        )
        merged_metadata = {}
        source_kinds = {}
        for grouped_candidate in grouped:
            merged_metadata.update(grouped_candidate.metadata)
            source_kinds.update(
                grouped_candidate.metadata.get("_source_kinds", {})
            )
            for message_id in grouped_candidate.source_message_ids:
                source_kinds.setdefault(
                    message_id, grouped_candidate.source_kind.value
                )
        if evidence is not None:
            source_kinds.update(
                {
                    message_id: evidence.source_kind.value
                    for message_id in evidence.source_message_ids
                }
            )
        merged_metadata["_source_kinds"] = source_kinds
        assertion = pending.candidate.model_copy(
            update={
                "content": "；".join(contents),
                "event_frame": frame,
                "source_message_ids": source_ids,
                "metadata": merged_metadata,
            }
        )
        resolver_candidate = assertion.model_copy(
            update={
                "metadata": {
                    **assertion.metadata,
                    "resolution_evidence": [
                        message.content for message in messages
                    ],
                }
            }
        )
        return assertion, resolver_candidate

    @staticmethod
    def _eligible_role(source_kind: SourceKind, message: Message) -> bool:
        if source_kind is SourceKind.TOOL_VERIFIED:
            return message.role == "tool"
        return message.role == "user"

    @staticmethod
    def _message_related(
        pending: PendingResolution, message: Message
    ) -> bool:
        content = message.content
        if any(
            marker in content
            for marker in (
                "就是",
                "那次",
                "之前",
                "说错",
                "后来",
                "取消",
                "不去",
            )
        ):
            return True
        frame = pending.candidate.event_frame
        return bool(
            frame
            and any(
                value != "unknown" and len(value) > 1 and value in content
                for value in (
                    frame.actor,
                    frame.predicate,
                    frame.object,
                    frame.location,
                )
            )
        )

    @staticmethod
    def _frames_related(
        left: EventFrame | None, right: EventFrame | None
    ) -> bool:
        if left is None or right is None:
            return False
        comparable = [
            (getattr(left, field), getattr(right, field))
            for field in ("predicate", "object", "location")
            if getattr(left, field) != "unknown"
            and getattr(right, field) != "unknown"
        ]
        return bool(comparable) and all(a == b for a, b in comparable)

    @classmethod
    def _group_frames_related(
        cls,
        grouped: tuple[CandidateMemory, ...],
        candidate: CandidateMemory,
    ) -> bool:
        return bool(grouped) and all(
            cls._frames_related(
                item.event_frame,
                candidate.event_frame,
            )
            for item in grouped
        )

    @staticmethod
    def _merge_messages(
        existing: tuple[Message, ...], incoming: tuple[Message, ...]
    ) -> tuple[Message, ...]:
        by_id = {message.message_id: message for message in existing}
        for message in incoming:
            by_id.setdefault(message.message_id, message)
        return tuple(by_id.values())

    @staticmethod
    def _validate_message_ids(
        existing: tuple[Message, ...], incoming: tuple[Message, ...]
    ) -> None:
        by_id = {message.message_id: message for message in existing}
        for message in incoming:
            previous = by_id.get(message.message_id)
            if previous is not None and previous != message:
                raise ValueError("message_id cannot be reused with different content")

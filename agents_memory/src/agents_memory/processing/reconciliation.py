from dataclasses import dataclass
from datetime import UTC, datetime

from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    MemoryRecord,
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
from agents_memory.processing.event_matching import frames_related
from agents_memory.processing.pending import missing_event_dimensions
from agents_memory.resolution.base import RelationResolver
from agents_memory.storage.repository import MemoryRepository


@dataclass(frozen=True)
class ReconciliationResult:
    plans: tuple[ActionPlan, ...] = ()
    consumed_candidate_indexes: frozenset[int] = frozenset()


@dataclass(frozen=True)
class _EvidenceSelection:
    """Evidence chosen for one pending item and its plan index."""

    candidate_index: int
    candidate: CandidateMemory | None
    messages: tuple[Message, ...]


class PendingResolutionReconciler:
    """Reconsider open assertions only when a write brings unused evidence."""

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
        """Produce plans; persistence remains the coordinator's responsibility."""

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
            new_messages = self._new_messages(pending, messages)
            if not new_messages:
                continue
            synthetic_index = len(candidates) + len(plans)
            lifecycle = self._lifecycle_plan(
                pending,
                synthetic_index,
                claimed_targets,
                now,
            )
            if lifecycle is not None:
                plans.append(lifecycle)
                continue

            evidence = self._select_evidence(
                pending,
                new_messages,
                event_candidates,
                consumed,
                synthetic_index,
            )
            if evidence is None:
                continue
            candidate, resolver_candidate = self._compose_assertion(
                pending,
                evidence.candidate,
                list(evidence.messages),
            )
            plans.append(
                self._resolved_plan(
                    pending=pending,
                    evidence=evidence,
                    candidate=candidate,
                    resolver_candidate=resolver_candidate,
                    active_targets=self._active_targets(pending),
                    resolver=resolver,
                    claimed_targets=claimed_targets,
                    now=now,
                )
            )
        return ReconciliationResult(tuple(plans), frozenset(consumed))

    def _new_messages(
        self,
        pending: PendingResolution,
        messages: list[Message],
    ) -> list[Message]:
        """Reject message-ID reuse and retain only evidence not seen before."""

        self._validate_message_ids(pending.source_messages, tuple(messages))
        return [
            message
            for message in messages
            if message.message_id not in pending.processed_evidence_message_ids
        ]

    @staticmethod
    def _lifecycle_plan(
        pending: PendingResolution,
        candidate_index: int,
        claimed_targets: set[str],
        now: datetime,
    ) -> ActionPlan | None:
        """Close pending work that cannot safely enter semantic resolution."""

        if pending.expires_at <= now:
            status = PendingResolutionStatus.EXPIRED
            reason = "pending resolution expired"
        elif claimed_targets & set(pending.conflicting_memory_ids):
            status = PendingResolutionStatus.OBSOLETE
            reason = "target superseded by another resolution"
        else:
            return None
        updated = pending.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "last_evaluated_at": now,
                "reason": reason if status is PendingResolutionStatus.OBSOLETE else pending.reason,
            }
        )
        return ActionPlan(
            candidate_index=candidate_index,
            candidate=pending.candidate,
            action=Action.NOOP,
            pending_resolution=updated,
            reason=reason,
        )

    def _select_evidence(
        self,
        pending: PendingResolution,
        new_messages: list[Message],
        event_candidates: list[tuple[int, CandidateMemory]],
        consumed: set[int],
        synthetic_index: int,
    ) -> _EvidenceSelection | None:
        """Prefer a structured event candidate, then fall back to raw messages."""

        evidence = next(
            (
                (index, candidate)
                for index, candidate in event_candidates
                if index not in consumed
                and frames_related(
                    pending.candidate.event_frame,
                    candidate.event_frame,
                )
            ),
            None,
        )
        if evidence is None:
            relevant = tuple(
                message
                for message in new_messages
                if self._eligible_role(pending.candidate.source_kind, message)
                and self._message_related(pending, message)
            )
            return (
                _EvidenceSelection(synthetic_index, None, relevant)
                if relevant
                else None
            )

        index, candidate = evidence
        consumed.add(index)
        evidence_ids = set(candidate.source_message_ids)
        relevant = tuple(
            message
            for message in new_messages
            if message.message_id in evidence_ids
            and self._eligible_role(candidate.source_kind, message)
        )
        if relevant:
            return _EvidenceSelection(index, candidate, relevant)
        consumed.remove(index)
        return None

    def _active_targets(
        self,
        pending: PendingResolution,
    ) -> list[MemoryRecord]:
        """Reload truth because targets may have changed while evidence waited."""

        return [
            target
            for target in self.repository.get_memories(
                list(pending.conflicting_memory_ids)
            )
            if target.validity is Validity.ACTIVE
        ]

    def _resolved_plan(
        self,
        *,
        pending: PendingResolution,
        evidence: _EvidenceSelection,
        candidate: CandidateMemory,
        resolver_candidate: CandidateMemory,
        active_targets: list[MemoryRecord],
        resolver: RelationResolver,
        claimed_targets: set[str],
        now: datetime,
    ) -> ActionPlan:
        """Re-decide against current truth and attach the pending transition."""

        merged_messages = self._merge_messages(
            pending.source_messages,
            evidence.messages,
        )
        processed = tuple(
            dict.fromkeys(
                (
                    *pending.processed_evidence_message_ids,
                    *(message.message_id for message in evidence.messages),
                )
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
            return ActionPlan(
                candidate_index=evidence.candidate_index,
                candidate=candidate,
                action=Action.NOOP,
                pending_resolution=updated,
                reason="deferred target is no longer active",
            )

        relations = resolver.resolve(resolver_candidate, active_targets)
        plan = self.decision_engine.decide(
            evidence.candidate_index,
            candidate,
            active_targets,
            relations,
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
        return plan.model_copy(update={"pending_resolution": updated})

    @classmethod
    def _compose_assertion(
        cls,
        pending: PendingResolution,
        evidence: CandidateMemory | None,
        messages: list[Message],
    ) -> tuple[CandidateMemory, CandidateMemory]:
        """Build the stored assertion and a resolver-only evidence-enriched view."""

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

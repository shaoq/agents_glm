from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents_memory.models import (
    Action,
    CandidateMemory,
    EventFrame,
    EventIdentity,
    EventStatus,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Message,
    PendingResolution,
    PendingResolutionStatus,
    RelationKind,
    RelationMatch,
    SourceKind,
    TemporalAnchor,
    TemporalGranularity,
    TemporalRelation,
    TemporalResolution,
    Validity,
)
from agents_memory.processing.decision import DecisionEngine
from agents_memory.processing.event_matching import (
    frames_related,
    group_frames_related,
)
from agents_memory.processing.reconciliation import PendingResolutionReconciler
from agents_memory.storage.repository import MemoryRepository


def event_candidate(
    content: str = "用户取消北京行程",
    status: EventStatus = EventStatus.CANCELLED,
) -> CandidateMemory:
    return CandidateMemory(
        content=content,
        type=MemoryType.EVENT,
        importance=8,
        confidence=0.9,
        source_message_ids=("new-message",),
        source_kind=SourceKind.USER_EXPLICIT,
        event_frame=EventFrame(
            actor="user",
            predicate="travel",
            object="北京",
            status=status,
        ),
    )


def seed(repository: MemoryRepository) -> tuple[MemoryScope, PendingResolution]:
    scope = MemoryScope(user_id="u1")
    old = MemoryRecord(
        id="old",
        scope=scope,
        type=MemoryType.EVENT,
        content="用户计划去北京",
        importance=8,
        confidence=0.9,
        validity=Validity.ACTIVE,
        event_frame=event_candidate(
            "用户计划去北京", EventStatus.PLANNED
        ).event_frame,
    )
    repository.save_memory(old)
    now = datetime.now(UTC)
    original_message = Message(
        message_id="original", role="user", content="不去了"
    )
    pending = PendingResolution(
        id="pr-1",
        scope=scope,
        candidate=event_candidate().model_copy(
            update={"source_message_ids": ("original",)}
        ),
        conflicting_memory_ids=("old",),
        semantic_relation=RelationKind.CONTRADICT,
        missing_dimensions=("event_time",),
        source_message_ids=("original",),
        source_messages=(original_message,),
        processed_evidence_message_ids=("original",),
        importance=8,
        status=PendingResolutionStatus.OPEN,
        created_at=now,
        updated_at=now,
        last_evaluated_at=now,
        expires_at=now + timedelta(days=30),
    )
    repository.save_pending_resolution(pending)
    return scope, pending


class Resolver:
    def __init__(
        self,
        identity: EventIdentity = EventIdentity.SAME_EVENT,
        relation: RelationKind = RelationKind.CONTRADICT,
    ) -> None:
        self.identity = identity
        self.relation = relation
        self.calls = 0

    def resolve(self, _candidate, histories):
        self.calls += 1
        return [
            RelationMatch(
                memory_id=item.id,
                relation=self.relation,
                identity=self.identity,
                temporal=TemporalRelation.SAME_WINDOW,
                explicit_correction=self.relation is RelationKind.CORRECT,
            )
            for item in histories
        ]


def test_reconciler_resolves_with_new_candidate_and_consumes_it(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()
    reconciler = PendingResolutionReconciler(repository, DecisionEngine())
    message = Message(
        message_id="new-message", role="user", content="北京机票退好了"
    )

    result = reconciler.reconcile(
        scope=scope,
        messages=[message],
        candidates=[event_candidate()],
        resolver=resolver,
    )

    assert resolver.calls == 1
    assert result.consumed_candidate_indexes == frozenset({0})
    assert result.plans[0].action is Action.UPDATE
    assert result.plans[0].pending_resolution.status is PendingResolutionStatus.RESOLVED


def test_reconciler_does_not_retry_without_new_evidence(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()
    reconciler = PendingResolutionReconciler(repository, DecisionEngine())

    result = reconciler.reconcile(
        scope=scope,
        messages=[Message(message_id="original", role="user", content="不去了")],
        candidates=[],
        resolver=resolver,
    )

    assert result.plans == ()
    assert resolver.calls == 0


def test_reconciler_uses_raw_message_when_extractor_returns_zero(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver(relation=RelationKind.CORRECT)
    reconciler = PendingResolutionReconciler(repository, DecisionEngine())

    result = reconciler.reconcile(
        scope=scope,
        messages=[
            Message(
                message_id="new-message",
                role="user",
                content="对，就是明天那次，我之前说错了",
            )
        ],
        candidates=[],
        resolver=resolver,
    )

    assert resolver.calls == 1
    assert result.plans[0].action is Action.UPDATE
    assert result.plans[0].relation is RelationKind.CORRECT


def test_reconciler_marks_pending_obsolete_when_target_is_no_longer_active(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    old = repository.get_memory("old")
    assert old is not None
    with repository._connect() as connection:
        connection.execute(
            "UPDATE memories SET validity = ? WHERE id = ?",
            (Validity.RETRACTED.value, old.id),
        )
    resolver = Resolver()

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[Message(message_id="new-message", role="user", content="就是那次")],
        candidates=[],
        resolver=resolver,
    )

    assert resolver.calls == 0
    assert result.plans[0].action is Action.NOOP
    assert (
        result.plans[0].pending_resolution.status
        is PendingResolutionStatus.OBSOLETE
    )


def test_reconciler_expires_pending_without_calling_resolver(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, pending = seed(repository)
    repository.save_pending_resolution(
        pending.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    )
    resolver = Resolver()

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[Message(message_id="new-message", role="user", content="后来")],
        candidates=[],
        resolver=resolver,
    )

    assert resolver.calls == 0
    assert result.plans[0].action is Action.NOOP
    assert (
        result.plans[0].pending_resolution.status
        is PendingResolutionStatus.EXPIRED
    )


def test_reconciler_ignores_unrelated_event_candidate(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()
    unrelated = event_candidate("用户完成上海行程").model_copy(
        update={
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="上海",
                status=EventStatus.COMPLETED,
            )
        }
    )

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[Message(message_id="new-message", role="user", content="去了上海")],
        candidates=[unrelated],
        resolver=resolver,
    )

    assert resolver.calls == 0
    assert result.plans == ()


def test_raw_message_resolution_uses_noncolliding_candidate_index(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()
    fact = event_candidate().model_copy(update={"type": MemoryType.FACT})

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[Message(message_id="new-message", role="user", content="就是那次")],
        candidates=[fact],
        resolver=resolver,
    )

    assert result.plans[0].candidate_index == 1


def test_reconciler_resolves_pending_assertion_not_evidence_candidate(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)

    class CapturingResolver(Resolver):
        def resolve(self, candidate, histories):
            self.candidate = candidate
            return super().resolve(candidate, histories)

    resolver = CapturingResolver()
    evidence = event_candidate(
        "北京行程是明天", EventStatus.PLANNED
    )

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[
            Message(
                message_id="new-message",
                role="user",
                content="北京行程是明天",
            )
        ],
        candidates=[evidence],
        resolver=resolver,
    )

    assert resolver.candidate.content == "用户取消北京行程"
    assert resolver.candidate.event_frame.status is EventStatus.CANCELLED
    assert result.plans[0].candidate.content == "用户取消北京行程"
    assert set(result.plans[0].candidate.source_message_ids) == {
        "original",
        "new-message",
    }


def test_reconciler_ignores_assistant_raw_message_for_user_assertion(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[
            Message(
                message_id="assistant-evidence",
                role="assistant",
                content="对，就是明天那次",
            )
        ],
        candidates=[],
        resolver=resolver,
    )

    assert result.plans == ()
    assert resolver.calls == 0


def test_reconciler_ignores_unrelated_greeting_without_candidate(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[Message(message_id="hello", role="user", content="你好")],
        candidates=[],
        resolver=resolver,
    )

    assert result.plans == ()
    assert resolver.calls == 0


def test_reconciler_rejects_reused_message_id_with_different_content(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)

    with pytest.raises(ValueError, match="message_id"):
        PendingResolutionReconciler(
            repository, DecisionEngine()
        ).reconcile(
            scope=scope,
            messages=[
                Message(
                    message_id="original",
                    role="user",
                    content="完全不同的新内容",
                )
            ],
            candidates=[],
            resolver=Resolver(),
        )


def test_sparse_frames_are_not_related_by_actor_alone() -> None:
    assert not frames_related(
        EventFrame(actor="user"),
        EventFrame(actor="user"),
    )
    assert not frames_related(None, None)


def test_group_frame_matching_rejects_conflict_hidden_by_sparse_first() -> None:
    sparse = event_candidate().model_copy(
        update={
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="unknown",
            )
        }
    )
    beijing = event_candidate()
    shanghai = event_candidate().model_copy(
        update={
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="上海",
            )
        }
    )

    assert not group_frames_related((sparse, beijing), shanghai)


def test_tool_verified_candidate_can_evidence_user_pending(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    resolver = Resolver()
    evidence = event_candidate("北京机票已退").model_copy(
        update={
            "source_message_ids": ("tool-evidence",),
            "source_kind": SourceKind.TOOL_VERIFIED,
        }
    )

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[
            Message(
                message_id="tool-evidence",
                role="tool",
                content="北京机票已退",
            )
        ],
        candidates=[evidence],
        resolver=resolver,
    )

    assert resolver.calls == 1
    assert result.plans[0].action is Action.UPDATE
    assert (
        result.plans[0].candidate.metadata["_source_kinds"]["tool-evidence"]
        == "tool_verified"
    )


def test_compose_assertion_merges_grouped_event_frames() -> None:
    now = datetime.now(UTC)
    first = event_candidate().model_copy(
        update={
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="unknown",
                status=EventStatus.CANCELLED,
            )
        }
    )
    second = event_candidate("用户取消北京行程")
    pending = PendingResolution(
        id="grouped",
        scope=MemoryScope(user_id="u1"),
        candidate=first,
        grouped_candidates=(first, second),
        semantic_relation=RelationKind.CONTRADICT,
        importance=8,
        expires_at=now + timedelta(days=1),
    )

    assertion, _ = PendingResolutionReconciler._compose_assertion(
        pending, None, []
    )

    assert assertion.event_frame.object == "北京"


def test_still_deferred_resolution_persists_enriched_assertion(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, _ = seed(repository)
    anchor = TemporalAnchor(
        raw_text="明天",
        start=datetime(2026, 7, 25, tzinfo=UTC),
        end=datetime(2026, 7, 26, tzinfo=UTC),
        granularity=TemporalGranularity.DAY,
        resolution=TemporalResolution.RESOLVED,
    )
    evidence = event_candidate("北京行程是明天").model_copy(
        update={
            "event_frame": event_candidate().event_frame.model_copy(
                update={"temporal_anchor": anchor}
            )
        }
    )
    resolver = Resolver(identity=EventIdentity.UNKNOWN)

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[
            Message(
                message_id="new-message",
                role="user",
                content="北京行程是明天",
            )
        ],
        candidates=[evidence],
        resolver=resolver,
    )

    updated = result.plans[0].pending_resolution
    assert updated.status is PendingResolutionStatus.OPEN
    assert (
        updated.candidate.event_frame.temporal_anchor.resolution
        is TemporalResolution.RESOLVED
    )
    assert "event_time" not in updated.missing_dimensions


def test_source_kind_mapping_survives_multiple_reconciliation_rounds() -> None:
    now = datetime.now(UTC)
    assertion = event_candidate().model_copy(
        update={
            "source_message_ids": ("original", "tool-evidence"),
            "metadata": {
                "_source_kinds": {
                    "original": "user_explicit",
                    "tool-evidence": "tool_verified",
                }
            },
        }
    )
    pending = PendingResolution(
        id="multi-round",
        scope=MemoryScope(user_id="u1"),
        candidate=assertion,
        grouped_candidates=(assertion,),
        semantic_relation=RelationKind.CONTRADICT,
        importance=8,
        expires_at=now + timedelta(days=1),
    )

    next_assertion, _ = PendingResolutionReconciler._compose_assertion(
        pending, None, []
    )

    assert (
        next_assertion.metadata["_source_kinds"]["tool-evidence"]
        == "tool_verified"
    )


def test_reconciler_serializes_pending_items_sharing_target(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    scope, pending = seed(repository)
    repository.save_pending_resolution(
        pending.model_copy(update={"id": "pr-2"})
    )
    resolver = Resolver()

    result = PendingResolutionReconciler(
        repository, DecisionEngine()
    ).reconcile(
        scope=scope,
        messages=[
            Message(message_id="new-message", role="user", content="就是那次")
        ],
        candidates=[],
        resolver=resolver,
    )

    assert [plan.action for plan in result.plans] == [Action.UPDATE, Action.NOOP]
    assert (
        result.plans[1].pending_resolution.status
        is PendingResolutionStatus.OBSOLETE
    )
    assert resolver.calls == 1

from datetime import UTC, datetime, timedelta

from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    EventFrame,
    MemoryScope,
    MemoryType,
    Message,
    RelationKind,
    SourceKind,
)
from agents_memory.processing.deferred import DeferredResolutionCollector
from agents_memory.processing.pending import PendingResolutionPolicy


def event_candidate(
    content: str,
    *,
    message_id: str,
    object_: str = "北京",
    importance: int = 6,
) -> CandidateMemory:
    return CandidateMemory(
        content=content,
        type=MemoryType.EVENT,
        importance=importance,
        confidence=0.9,
        source_message_ids=(message_id,),
        source_kind=SourceKind.USER_EXPLICIT,
        event_frame=EventFrame(
            actor="user",
            predicate="travel",
            object=object_,
        ),
    )


def defer_plan(index: int, candidate: CandidateMemory, *targets: str) -> ActionPlan:
    return ActionPlan(
        candidate_index=index,
        candidate=candidate,
        action=Action.DEFER,
        target_ids=targets,
        relation=RelationKind.CONTRADICT,
        reason="identity unknown",
    )


def test_collector_creates_pending_with_explicit_clock_and_policy() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    candidate = event_candidate("可能取消北京行程", message_id="m1")
    message = Message(message_id="m1", role="user", content="可能不去了")
    groups = []

    pending = DeferredResolutionCollector(PendingResolutionPolicy(normal_days=5)).collect(
        groups,
        scope=MemoryScope(user_id="u1"),
        plan=defer_plan(0, candidate, "old"),
        messages=[message],
        now=now,
    )

    assert groups == [pending]
    assert pending.grouped_candidates == (candidate,)
    assert pending.conflicting_memory_ids == ("old",)
    assert pending.source_messages == (message,)
    assert pending.processed_evidence_message_ids == ("m1",)
    assert pending.expires_at == now + timedelta(days=5)


def test_collector_merges_by_target_and_preserves_highest_importance() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    first = event_candidate("可能取消北京行程", message_id="m1", importance=5)
    second = event_candidate(
        "后来不去上海",
        message_id="m2",
        object_="上海",
        importance=9,
    )
    messages = [
        Message(message_id="m1", role="user", content="可能取消"),
        Message(message_id="m2", role="user", content="后来不去"),
    ]
    groups = []
    collector = DeferredResolutionCollector(PendingResolutionPolicy())
    original = collector.collect(
        groups,
        scope=MemoryScope(user_id="u1"),
        plan=defer_plan(0, first, "shared"),
        messages=messages,
        now=now,
    )

    merged = collector.collect(
        groups,
        scope=MemoryScope(user_id="u1"),
        plan=defer_plan(1, second, "shared", "other"),
        messages=messages,
        now=now + timedelta(hours=1),
    )

    assert merged.id == original.id
    assert groups == [merged]
    assert merged.grouped_candidates == (first, second)
    assert merged.conflicting_memory_ids == ("shared", "other")
    assert merged.source_message_ids == ("m1", "m2")
    assert merged.processed_evidence_message_ids == ("m1", "m2")
    assert merged.source_messages == tuple(messages)
    assert merged.importance == 9


def test_collector_merges_related_frames_but_keeps_unrelated_groups_separate() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    beijing_1 = event_candidate("北京行程待定", message_id="m1")
    beijing_2 = event_candidate("北京行程取消", message_id="m2")
    shanghai = event_candidate(
        "上海行程取消",
        message_id="m3",
        object_="上海",
    )
    messages = [
        Message(message_id="m1", role="user", content="待定"),
        Message(message_id="m2", role="user", content="取消"),
        Message(message_id="m3", role="user", content="另一次取消"),
    ]
    groups = []
    collector = DeferredResolutionCollector(PendingResolutionPolicy())

    first = collector.collect(
        groups,
        scope=MemoryScope(user_id="u1"),
        plan=defer_plan(0, beijing_1, "old-1"),
        messages=messages,
        now=now,
    )
    related = collector.collect(
        groups,
        scope=MemoryScope(user_id="u1"),
        plan=defer_plan(1, beijing_2, "old-2"),
        messages=messages,
        now=now,
    )
    unrelated = collector.collect(
        groups,
        scope=MemoryScope(user_id="u1"),
        plan=defer_plan(2, shanghai, "old-3"),
        messages=messages,
        now=now,
    )

    assert related.id == first.id
    assert unrelated.id != first.id
    assert len(groups) == 2

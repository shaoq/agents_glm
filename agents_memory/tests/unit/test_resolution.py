from types import SimpleNamespace

import pytest

from agents_memory.models import (
    CandidateMemory,
    EventFrame,
    EventIdentity,
    EventStatus,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    SourceKind,
    TemporalRelation,
)
from agents_memory.resolution.llm import LLMRelationResolver, RelationOutputError


def candidate() -> CandidateMemory:
    return CandidateMemory(
        content="用户搬到北京",
        type=MemoryType.FACT,
        importance=8,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
    )


def history(memory_id: str) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id="u1"),
        type=MemoryType.FACT,
        content="用户住在上海",
        importance=8,
        confidence=0.9,
    )


def event_candidate() -> CandidateMemory:
    return candidate().model_copy(
        update={
            "content": "用户取消明天的北京行程",
            "type": MemoryType.EVENT,
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="北京",
                status=EventStatus.CANCELLED,
            ),
        }
    )


def event_history(memory_id: str = "old") -> MemoryRecord:
    return history(memory_id).model_copy(
        update={
            "content": "用户计划明天去北京",
            "type": MemoryType.EVENT,
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="北京",
                status=EventStatus.PLANNED,
            ),
        }
    )


def client_with(content: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: response)
        )
    )


def test_relation_resolver_parses_whole_top_k_once() -> None:
    calls = 0
    client = client_with(
        '{"relations":[{"memory_id":"old","relation":"contradict","reason":"住址变化"}]}'
    )
    original = client.chat.completions.create

    def counted(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    client.chat.completions.create = counted
    result = LLMRelationResolver(client=client, model="flash").resolve(
        candidate(), [history("old")]
    )

    assert result[0].relation.value == "contradict"
    assert calls == 1


def test_relation_resolver_rejects_unknown_memory_id() -> None:
    resolver = LLMRelationResolver(
        client=client_with(
            '{"relations":[{"memory_id":"invented","relation":"duplicate"}]}'
        ),
        model="flash",
    )

    with pytest.raises(RelationOutputError):
        resolver.resolve(candidate(), [history("old")])


def test_relation_resolver_requires_one_result_per_history() -> None:
    resolver = LLMRelationResolver(
        client=client_with('{"relations":[]}'),
        model="flash",
    )

    with pytest.raises(RelationOutputError):
        resolver.resolve(candidate(), [history("old")])


def test_relation_resolver_repairs_semantically_invalid_output_once() -> None:
    outputs = iter(
        [
            '{"relations":[{"memory_id":"invented","relation":"duplicate"}]}',
            '{"relations":[{"memory_id":"old","relation":"contradict"}]}',
        ]
    )
    calls = 0

    def create(**_):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(outputs)))]
        )

    resolver = LLMRelationResolver(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        model="flash",
    )

    assert resolver.resolve(candidate(), [history("old")])[0].memory_id == "old"
    assert calls == 2


def test_event_relation_resolver_parses_multidimensional_relation() -> None:
    result = LLMRelationResolver(
        client=client_with(
            """
            {"relations":[{
              "memory_id":"old",
              "relation":"contradict",
              "identity":"same_event",
              "temporal":"same_window",
              "confidence":0.92,
              "reason":"同一北京行程状态变化"
            }]}
            """
        ),
        model="flash",
    ).resolve(event_candidate(), [event_history()])

    assert result[0].identity is EventIdentity.SAME_EVENT
    assert result[0].temporal is TemporalRelation.SAME_WINDOW
    assert result[0].confidence == 0.92


def test_event_relation_resolver_requires_identity_and_temporal_dimensions() -> None:
    resolver = LLMRelationResolver(
        client=client_with(
            '{"relations":[{"memory_id":"old","relation":"contradict"}]}'
        ),
        model="flash",
    )

    with pytest.raises(RelationOutputError):
        resolver.resolve(event_candidate(), [event_history()])


def test_event_relation_payload_contains_history_event_frame() -> None:
    payload = {}

    def create(**kwargs):
        payload.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"relations":[{"memory_id":"old",'
                            '"relation":"none","identity":"different_event",'
                            '"temporal":"after"}]}'
                        )
                    )
                )
            ]
        )

    resolver = LLMRelationResolver(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        model="flash",
    )
    resolver.resolve(event_candidate(), [event_history()])

    assert "event_frame" in payload["messages"][1]["content"]

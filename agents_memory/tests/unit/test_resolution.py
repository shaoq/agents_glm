from types import SimpleNamespace

import pytest

from agents_memory.models import CandidateMemory, MemoryRecord, MemoryScope, MemoryType, SourceKind
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

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from agents_memory.extraction.llm import LLMFactExtractor, SourceAttributionError
from agents_memory.models import Message, SourceKind


def response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_extractor_parses_candidates_and_preserves_uncertainty() -> None:
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: response(
                    """
                    {"candidates": [{
                      "content": "用户可能以后会学习 Rust",
                      "type": "fact",
                      "importance": 5,
                      "confidence": 0.7,
                      "source_message_ids": ["m1"],
                      "source_kind": "user_explicit"
                    }]}
                    """
                )
            )
        )
    )
    extractor = LLMFactExtractor(client=client, model="flash")

    result = extractor.extract([Message(message_id="m1", role="user", content="我可能学 Rust")])

    assert result[0].content == "用户可能以后会学习 Rust"
    assert result[0].source_kind is SourceKind.USER_EXPLICIT


def test_extractor_retries_once_for_invalid_schema() -> None:
    outputs = iter([response("not json"), response('{"candidates": []}')])
    calls = 0

    def create(**_):
        nonlocal calls
        calls += 1
        return next(outputs)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert LLMFactExtractor(client=client, model="flash").extract([]) == []
    assert calls == 2


def test_extractor_retries_transient_network_error() -> None:
    outputs = iter(
        [
            APIConnectionError(request=httpx.Request("POST", "https://example.test")),
            response('{"candidates": []}'),
        ]
    )
    calls = 0

    def create(**_):
        nonlocal calls
        calls += 1
        value = next(outputs)
        if isinstance(value, Exception):
            raise value
        return value

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert LLMFactExtractor(client=client, model="flash").extract([]) == []
    assert calls == 2


def test_extractor_rejects_assistant_as_user_fact_source() -> None:
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: response(
                    """
                    {"candidates": [{
                      "content": "用户决定使用 Rust",
                      "type": "fact",
                      "importance": 8,
                      "confidence": 0.9,
                      "source_message_ids": ["a1"],
                      "source_kind": "user_explicit"
                    }]}
                    """
                )
            )
        )
    )
    extractor = LLMFactExtractor(client=client, model="flash")

    with pytest.raises(SourceAttributionError):
        extractor.extract(
            [Message(message_id="a1", role="assistant", content="建议你使用 Rust")]
        )


def test_user_explicit_candidate_cannot_mix_assistant_source() -> None:
    extractor = LLMFactExtractor(
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: response(
                        """
                        {"candidates": [{
                          "content": "用户决定使用 Rust", "type": "fact",
                          "importance": 8, "confidence": 0.9,
                          "source_message_ids": ["u1", "a1"],
                          "source_kind": "user_explicit"
                        }]}
                        """
                    )
                )
            )
        ),
        model="flash",
    )

    with pytest.raises(SourceAttributionError):
        extractor.extract(
            [
                Message(message_id="u1", role="user", content="我在考虑"),
                Message(message_id="a1", role="assistant", content="你已决定"),
            ]
        )


def test_extractor_rejects_duplicate_input_message_ids() -> None:
    extractor = LLMFactExtractor(
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: response('{"candidates": []}')
                )
            )
        ),
        model="flash",
    )

    with pytest.raises(SourceAttributionError):
        extractor.extract(
            [
                Message(message_id="same", role="user", content="a"),
                Message(message_id="same", role="assistant", content="b"),
            ]
        )

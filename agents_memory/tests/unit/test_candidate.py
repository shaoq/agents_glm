import pytest

from agents_memory.extraction.llm import SourceAttributionError
from agents_memory.models import CandidateMemory, MemoryType, Message, SourceKind
from agents_memory.processing.candidate import CandidateProcessor


def candidate(content: str, source: str = "m1") -> CandidateMemory:
    return CandidateMemory(
        content=content,
        type=MemoryType.FACT,
        importance=7,
        confidence=0.9,
        source_message_ids=(source,),
        source_kind=SourceKind.USER_EXPLICIT,
    )


def test_candidate_processor_deduplicates_safe_normalized_text() -> None:
    processor = CandidateProcessor()
    messages = [Message(message_id="m1", role="user", content="x")]

    result = processor.process(
        [candidate("用户  偏好 Python"), candidate("用户 偏好 Python")],
        messages,
    )

    assert [item.content for item in result.candidates] == ["用户 偏好 Python"]
    assert result.filtered_count == 1


def test_candidate_processor_preserves_negation() -> None:
    processor = CandidateProcessor()
    messages = [Message(message_id="m1", role="user", content="x")]

    result = processor.process(
        [candidate("用户喜欢 Java"), candidate("用户不喜欢 Java")],
        messages,
    )

    assert len(result.candidates) == 2


def test_candidate_processor_rejects_unknown_message_id() -> None:
    with pytest.raises(SourceAttributionError):
        CandidateProcessor().process(
            [candidate("用户偏好 Python", source="missing")],
            [Message(message_id="m1", role="user", content="x")],
        )

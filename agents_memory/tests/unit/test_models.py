from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents_memory.models import (
    Action,
    CandidateMemory,
    CandidateResult,
    ErrorCode,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    MemoryType,
    RelationKind,
    SourceKind,
    Validity,
    WriteReport,
    WriteStatus,
)


def test_candidate_memory_is_validated_and_frozen() -> None:
    candidate = CandidateMemory(
        content="用户偏好 Python",
        type=MemoryType.FACT,
        importance=8,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
    )

    assert candidate.importance == 8
    with pytest.raises(ValidationError):
        candidate.content = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [("importance", 0), ("importance", 11), ("confidence", -0.1), ("confidence", 1.1)],
)
def test_candidate_rejects_out_of_range_values(field: str, value: float) -> None:
    values = {
        "content": "用户偏好 Python",
        "type": MemoryType.FACT,
        "importance": 5,
        "confidence": 0.8,
        "source_message_ids": ("m1",),
        "source_kind": SourceKind.USER_EXPLICIT,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        CandidateMemory.model_validate(values)


def test_scope_requires_user_and_is_exact() -> None:
    scope = MemoryScope(user_id="u1", agent_id=None, session_id=None)

    assert scope.as_key() == ("u1", None, None)
    with pytest.raises(ValidationError):
        MemoryScope(user_id="")


def test_record_source_and_relation_contracts() -> None:
    now = datetime.now(UTC)
    scope = MemoryScope(user_id="u1")
    record = MemoryRecord(
        id="mem-1",
        scope=scope,
        type=MemoryType.FACT,
        content="用户偏好 Python",
        importance=8,
        confidence=0.9,
        validity=Validity.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    source = MemorySource(
        memory_id=record.id,
        message_id="m1",
        role="user",
        source_kind=SourceKind.USER_EXPLICIT,
        excerpt="我喜欢 Python",
        created_at=now,
    )
    relation = MemoryRelation(
        from_memory_id="mem-old",
        to_memory_id=record.id,
        relation=RelationKind.SUPERSEDES,
        created_at=now,
    )

    assert record.scope.user_id == "u1"
    assert source.memory_id == record.id
    assert relation.relation is RelationKind.SUPERSEDES


def test_write_report_serializes_stable_error_contract() -> None:
    result = CandidateResult(
        candidate_index=0,
        content="用户偏好 Python",
        action=Action.ADD,
        memory_id="mem-1",
    )
    report = WriteReport(
        request_id="req-1",
        status=WriteStatus.RETRYABLE,
        results=(result,),
        sqlite_committed=True,
        index_synced=False,
        error_code=ErrorCode.INDEX_UNAVAILABLE,
        error_message="temporary",
    )

    payload = report.model_dump(mode="json")

    assert payload["status"] == "retryable"
    assert payload["results"][0]["action"] == "add"
    assert payload["error_code"] == "index_unavailable"

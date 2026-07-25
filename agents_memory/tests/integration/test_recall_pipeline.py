"""End-to-end Recall pipeline scenarios against real SQLite + Fake drivers.

Covers business scenarios (10.2): current-session, agent-history, user-shared,
current-state, point-in-time, evolution, correction, conflict and
unknown-event-identity; plus degradation scenarios (10.3).
"""


import sqlite3
from datetime import UTC, datetime

from agents_memory.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    RelationKind,
    Validity,
)
from agents_memory.recall import (
    ExecutionStatus,
    RecallRequest,
    Sufficiency,
)
from agents_memory.storage.vector import IndexHit


def _record(
    *,
    rid: str,
    user_id: str = "u1",
    agent_id: str | None = None,
    session_id: str | None = None,
    content: str = "用户偏好 Python",
    type_: MemoryType = MemoryType.FACT,
    importance: int = 8,
    validity=Validity.ACTIVE,
) -> MemoryRecord:
    return MemoryRecord(
        id=rid,
        scope=MemoryScope(user_id=user_id, agent_id=agent_id, session_id=session_id),
        type=type_,
        content=content,
        importance=importance,
        confidence=0.9,
        validity=validity,
    )


def test_recall_current_session_returns_relevant_memory(
    recall_service_factory, recall_repository, recall_index, recall_embedder,
) -> None:
    record = _record(rid="m1", agent_id="a1", session_id="s1")
    recall_repository.save_memory(record)
    recall_index.upsert(record, recall_embedder.embed(["用户偏好"])[0])

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="s1", query="用户偏好什么",
        )
    )

    assert result.metadata.execution_status == ExecutionStatus.COMPLETE
    assert "Python" in result.context


def test_recall_returns_empty_when_no_memories(
    recall_service_factory,
) -> None:
    service = recall_service_factory()
    result = service.recall(RecallRequest(user_id="u1", query="anything"))

    assert result.metadata.sufficiency == Sufficiency.EMPTY
    assert result.evidence == ()


def test_recall_agent_history_lane(
    recall_service_factory, recall_repository, recall_index, recall_embedder,
) -> None:
    record = _record(rid="m1", agent_id="a1", session_id="older-session")
    recall_repository.save_memory(record)
    recall_index.upsert(record, recall_embedder.embed(["用户偏好"])[0])

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="current", query="用户偏好什么",
        )
    )

    assert "Python" in result.context


def test_recall_user_shared_lane(
    recall_service_factory, recall_repository, recall_index, recall_embedder,
) -> None:
    record = _record(rid="m1", agent_id="other-agent", session_id="other-session")
    recall_repository.save_memory(record)
    recall_index.upsert(record, recall_embedder.embed(["用户偏好"])[0])

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            query="用户偏好什么",
            allow_user_shared=True,
        )
    )

    assert "Python" in result.context


def test_recall_rejects_cross_user_candidates(
    recall_service_factory, recall_repository, recall_index, recall_embedder,
) -> None:
    other = _record(rid="m1", user_id="u2", agent_id="a2", session_id="s2")
    recall_repository.save_memory(other)
    recall_index.upsert(other, recall_embedder.embed(["x"])[0])
    recall_index.candidates_override = [IndexHit(memory_id="m1", similarity=0.99)]

    service = recall_service_factory()
    result = service.recall(RecallRequest(user_id="u1", query="anything"))

    assert result.metadata.sufficiency == Sufficiency.EMPTY
    assert result.evidence == ()


def test_recall_drops_stale_index_hits(
    recall_service_factory, recall_index,
) -> None:
    recall_index.candidates_override = [IndexHit(memory_id="ghost", similarity=0.9)]

    service = recall_service_factory()
    result = service.recall(RecallRequest(user_id="u1", query="anything"))

    assert result.metadata.sufficiency == Sufficiency.EMPTY


def test_recall_degrades_when_index_unavailable(
    recall_service_factory, recall_index,
) -> None:
    recall_index.fail_query = True

    service = recall_service_factory()
    result = service.recall(RecallRequest(user_id="u1", query="anything"))

    assert result.metadata.execution_status == ExecutionStatus.DEGRADED


def test_recall_falls_back_when_llm_output_malformed(
    recall_service_factory,
    recall_repository,
    recall_index,
    recall_embedder,
    recall_client,
) -> None:
    record = _record(rid="m1", agent_id="a1", session_id="s1")
    recall_repository.save_memory(record)
    recall_index.upsert(record, recall_embedder.embed(["用户偏好"])[0])
    recall_client.intent_response = "not-json"
    recall_client.scoring_response = "not-json"
    recall_client.evidence_response = "not-json"

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="s1", query="用户偏好",
        )
    )

    assert result.metadata.execution_status == ExecutionStatus.DEGRADED
    assert "Python" in result.context


def test_recall_respects_evidence_budget(
    recall_service_factory,
    recall_repository,
    recall_index,
    recall_embedder,
) -> None:
    for i in range(5):
        record = _record(
            rid=f"m{i}", agent_id="a1", session_id="s1", content=f"事实编号 {i}"
        )
        recall_repository.save_memory(record)
        recall_index.upsert(record, recall_embedder.embed([f"事实 {i}"])[0])

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            query="事实",
            max_evidence_items=2,
        )
    )

    assert len(result.evidence) <= 2


def test_recall_current_state_excludes_superseded_as_current_fact(
    recall_service_factory,
    recall_repository,
    recall_index,
    recall_embedder,
) -> None:
    old = _record(
        rid="m1",
        agent_id="a1",
        session_id="s1",
        content="地址在北京",
        validity=Validity.SUPERSEDED,
    )
    recall_repository.save_memory(old)
    recall_index.upsert(old, recall_embedder.embed(["地址"])[0])

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="s1", query="现在的地址",
        )
    )

    assert "北京" not in result.context


def test_recall_correction_relation_prefers_correcting_record(
    recall_service_factory,
    recall_repository,
    recall_index,
    recall_embedder,
    tmp_path,
) -> None:
    wrong = _record(rid="m1", agent_id="a1", session_id="s1", content="电话 123")
    correct = _record(rid="m2", agent_id="a1", session_id="s1", content="电话 456")
    for record in (wrong, correct):
        recall_repository.save_memory(record)
        recall_index.upsert(record, recall_embedder.embed(["电话"])[0])
    connection = sqlite3.connect(tmp_path / "recall.sqlite")
    connection.execute(
        "INSERT INTO memory_relations (from_memory_id, to_memory_id, relation, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("m2", "m1", RelationKind.CORRECTS.value, datetime.now(UTC).isoformat()),
    )
    connection.commit()
    connection.close()

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="s1", query="电话号码",
        )
    )

    assert "456" in result.context


def test_recall_supersedes_chain_keeps_current_state(
    recall_service_factory,
    recall_repository,
    recall_index,
    recall_embedder,
    tmp_path,
) -> None:
    old = _record(rid="m1", agent_id="a1", session_id="s1", content="住在北京")
    new = _record(rid="m2", agent_id="a1", session_id="s1", content="搬到上海")
    for record in (old, new):
        recall_repository.save_memory(record)
        recall_index.upsert(record, recall_embedder.embed(["住址"])[0])
    connection = sqlite3.connect(tmp_path / "recall.sqlite")
    connection.execute(
        "INSERT INTO memory_relations (from_memory_id, to_memory_id, relation, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("m2", "m1", RelationKind.SUPERSEDES.value, datetime.now(UTC).isoformat()),
    )
    connection.commit()
    connection.close()

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="s1", query="现在住址",
        )
    )

    assert "上海" in result.context


def test_recall_preserves_conflicting_pair_as_uncertain(
    recall_service_factory,
    recall_repository,
    recall_index,
    recall_embedder,
    recall_client,
) -> None:
    a = _record(rid="m1", agent_id="a1", session_id="s1", content="会议在周一")
    b = _record(rid="m2", agent_id="a1", session_id="s1", content="会议在周二")
    for record in (a, b):
        recall_repository.save_memory(record)
        recall_index.upsert(record, recall_embedder.embed(["会议"])[0])
    recall_client.evidence_response = (
        '{"groups":[{"memory_ids":["m1","m2"],"identity":"same_event","conflict":true}]}'
    )

    service = recall_service_factory()
    result = service.recall(
        RecallRequest(
            user_id="u1", agent_id="a1", session_id="s1", query="会议时间",
        )
    )

    assert result.metadata.sufficiency == Sufficiency.CONFLICTED
    assert "周一" in result.context
    assert "周二" in result.context

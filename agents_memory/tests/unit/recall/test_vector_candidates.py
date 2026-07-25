"""Tests for the Recall-specific MemoryIndex.query_candidates contract.

Covers task 2.9/2.10: an additive Recall query that takes a required
user_id, optional agent/session filters, multiple types, an allowed
validity range and a candidate cap. Existing Write query() is untouched.
"""

import pytest

from agents_memory.models import MemoryRecord, MemoryScope, MemoryType, Validity
from agents_memory.storage.vector import ChromaMemoryIndex


def _record(
    memory_id: str,
    *,
    user_id: str = "u1",
    agent_id: str | None = "a1",
    session_id: str | None = "s1",
    type_: MemoryType = MemoryType.FACT,
    validity: Validity = Validity.ACTIVE,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id, agent_id=agent_id, session_id=session_id),
        type=type_,
        content=memory_id,
        importance=5,
        confidence=0.8,
        validity=validity,
    )


@pytest.fixture()
def index(tmp_path) -> ChromaMemoryIndex:
    return ChromaMemoryIndex(tmp_path / "chroma", model="test", dimension=3)


class TestQueryCandidates:
    def test_filters_by_types(self, index):
        index.upsert(_record("f1", type_=MemoryType.FACT), [1.0, 0.0, 0.0])
        index.upsert(_record("e1", type_=MemoryType.EVENT), [1.0, 0.0, 0.0])
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT,),
            top_k=10,
        )
        assert {h.memory_id for h in hits} == {"f1"}

    def test_accepts_multiple_types(self, index):
        index.upsert(_record("f1", type_=MemoryType.FACT), [1.0, 0.0, 0.0])
        index.upsert(_record("e1", type_=MemoryType.EVENT), [1.0, 0.0, 0.0])
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT, MemoryType.EVENT),
            top_k=10,
        )
        assert {h.memory_id for h in hits} == {"f1", "e1"}

    def test_enforces_user_boundary(self, index):
        index.upsert(_record("mine", user_id="u1"), [1.0, 0.0, 0.0])
        index.upsert(
            _record("theirs", user_id="other", agent_id="", session_id=""),
            [1.0, 0.0, 0.0],
        )
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT,),
            top_k=10,
        )
        assert {h.memory_id for h in hits} == {"mine"}

    def test_optional_agent_filter(self, index):
        index.upsert(_record("a1m", agent_id="a1"), [1.0, 0.0, 0.0])
        index.upsert(_record("a2m", agent_id="a2"), [1.0, 0.0, 0.0])
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            agent_id="a1",
            types=(MemoryType.FACT,),
            top_k=10,
        )
        assert {h.memory_id for h in hits} == {"a1m"}

    def test_optional_session_filter(self, index):
        index.upsert(_record("s1m", session_id="s1"), [1.0, 0.0, 0.0])
        index.upsert(_record("s2m", session_id="s2"), [1.0, 0.0, 0.0])
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            session_id="s1",
            types=(MemoryType.FACT,),
            top_k=10,
        )
        assert {h.memory_id for h in hits} == {"s1m"}

    def test_validity_range_includes_superseded(self, index):
        index.upsert(_record("cur", validity=Validity.ACTIVE), [1.0, 0.0, 0.0])
        index.upsert(_record("old", validity=Validity.SUPERSEDED), [1.0, 0.0, 0.0])
        active_only = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT,),
            top_k=10,
            validities=(Validity.ACTIVE,),
        )
        assert {h.memory_id for h in active_only} == {"cur"}
        with_history = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT,),
            top_k=10,
            validities=(Validity.ACTIVE, Validity.SUPERSEDED),
        )
        assert {h.memory_id for h in with_history} == {"cur", "old"}

    def test_top_k_caps_candidates(self, index):
        for i in range(5):
            index.upsert(_record(f"m{i}"), [1.0, 0.0, 0.0])
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT,),
            top_k=2,
        )
        assert len(hits) <= 2

    def test_threshold_filters_low_similarity(self, index):
        index.upsert(_record("near"), [1.0, 0.0, 0.0])
        index.upsert(_record("far"), [0.6, 0.8, 0.0])
        hits = index.query_candidates(
            [1.0, 0.0, 0.0],
            user_id="u1",
            types=(MemoryType.FACT,),
            top_k=10,
            threshold=0.7,
        )
        assert {h.memory_id for h in hits} == {"near"}

    def test_existing_write_query_still_works(self, index):
        """Additive contract must not change Write query() semantics."""
        index.upsert(_record("m1"), [1.0, 0.0, 0.0])
        hits = index.query(
            [1.0, 0.0, 0.0],
            scope=MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
            type=MemoryType.FACT,
            top_k=10,
            threshold=0.0,
        )
        assert {h.memory_id for h in hits} == {"m1"}

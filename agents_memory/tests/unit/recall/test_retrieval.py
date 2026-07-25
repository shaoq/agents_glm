"""Tests for MultiLaneCandidateRetriever: multi-path generation, merge, caps
and degradation (task 4.1-4.6).
"""

import pytest

from agents_memory.embedding.base import FakeEmbedder
from agents_memory.models import (
    IndexOperationKind,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    RelationKind,
)
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    DegradationCode,
    QueryVariant,
    RecallIntent,
    RecallRequest,
    TemporalIntent,
)
from agents_memory.recall.planning import DeterministicPlanner
from agents_memory.recall.retrieval import MultiLaneCandidateRetriever
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import ChromaMemoryIndex

_DIM = 8


def _record(
    memory_id: str,
    *,
    content: str = "c",
    user_id: str = "u1",
    agent_id: str | None = "a1",
    session_id: str | None = "s1",
    type_: MemoryType = MemoryType.FACT,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id, agent_id=agent_id, session_id=session_id),
        type=type_,
        content=content,
        importance=5,
        confidence=0.8,
    )


def _request(**kwargs) -> RecallRequest:
    defaults: dict = {"user_id": "u1", "agent_id": "a1", "session_id": "s1", "query": "q"}
    defaults.update(kwargs)
    return RecallRequest(**defaults)


def _intent(query: str = "q", **kwargs) -> RecallIntent:
    base: dict = {"primary_query": query, "query_variants": (QueryVariant(text=query),)}
    base.update(kwargs)
    return RecallIntent(**base)


@pytest.fixture()
def repo(tmp_path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "test.db")


@pytest.fixture()
def index(tmp_path) -> ChromaMemoryIndex:
    return ChromaMemoryIndex(tmp_path / "chroma", model="test", dimension=_DIM)


@pytest.fixture()
def embedder() -> FakeEmbedder:
    return FakeEmbedder(dimension=_DIM)


@pytest.fixture()
def retriever(repo, index, embedder) -> MultiLaneCandidateRetriever:
    return MultiLaneCandidateRetriever(embedder, index, RecallReadRepository(repo.path))


def _embed(embedder: FakeEmbedder, text: str) -> list[float]:
    return embedder.embed([text])[0]


class TestSemanticRetrieval:
    def test_returns_matching_record(self, repo, index, embedder, retriever):
        record = _record("m1", content="the auth decision")
        repo.save_memory(record)
        index.upsert(record, _embed(embedder, "the auth decision"))
        plan = DeterministicPlanner().plan(
            _request(), _intent("the auth decision"), RecallDiagnostics()
        )
        candidates = retriever.retrieve(_request(), plan, RecallDiagnostics())
        assert "m1" in {c.memory_id for c in candidates}

    def test_multi_query_variants_merge_hits(self, repo, index, embedder, retriever):
        record = _record("m1", content="shared content")
        repo.save_memory(record)
        index.upsert(record, _embed(embedder, "shared content"))
        intent = RecallIntent(
            primary_query="shared content",
            query_variants=(
                QueryVariant(text="shared content"),
                QueryVariant(text="shared content"),
            ),
        )
        plan = DeterministicPlanner().plan(_request(), intent, RecallDiagnostics())
        candidates = retriever.retrieve(_request(), plan, RecallDiagnostics())
        m1 = next(c for c in candidates if c.memory_id == "m1")
        assert len(m1.hits) >= 2

    def test_global_candidate_limit_caps_results(self, repo, index, embedder):
        retriever = MultiLaneCandidateRetriever(
            embedder,
            index,
            RecallReadRepository(repo.path),
        )
        for i in range(5):
            record = _record(f"m{i}", content=f"content {i}")
            repo.save_memory(record)
            index.upsert(record, _embed(embedder, f"content {i}"))
        intent = RecallIntent(
            primary_query="content 0",
            query_variants=tuple(QueryVariant(text=f"content {i}") for i in range(5)),
        )
        plan = DeterministicPlanner().plan(_request(), intent, RecallDiagnostics())
        candidates = retriever.retrieve(_request(), plan, RecallDiagnostics())
        assert len(candidates) <= plan.global_candidate_limit


class TestTemporalAndUnsyncedPaths:
    def test_temporal_path_adds_candidates_without_index(self, repo, index, embedder, retriever):
        from datetime import UTC, datetime

        record = _record("m1", content="old fact").model_copy(
            update={"valid_from": datetime(2026, 1, 1, tzinfo=UTC)}
        )
        repo.save_memory(record)
        # Not upserted to the index: only the temporal path can surface it.
        intent = _intent("fact", temporal_need=TemporalIntent.CURRENT_STATE)
        plan = DeterministicPlanner().plan(_request(), intent, RecallDiagnostics())
        candidates = retriever.retrieve(_request(), plan, RecallDiagnostics())
        assert "m1" in {c.memory_id for c in candidates}

    def test_unsynced_coverage_adds_pending_records(self, repo, index, embedder, retriever):
        record = _record("m1", content="pending fact")
        repo.save_memory(record)
        repo.enqueue_index_operation("req-1", "m1", IndexOperationKind.UPSERT)
        intent = _intent("pending fact")
        plan = DeterministicPlanner().plan(_request(), intent, RecallDiagnostics())
        candidates = retriever.retrieve(_request(), plan, RecallDiagnostics())
        assert "m1" in {c.memory_id for c in candidates}


class TestRelationExpansion:
    def test_one_hop_relation_brings_neighbor(self, repo, index, embedder, retriever):
        seed = _record("seed", content="seed content")
        neighbor = _record("neighbor", content="neighbor content")
        repo.save_memory(seed)
        repo.save_memory(neighbor)
        index.upsert(seed, _embed(embedder, "seed content"))
        # neighbor not in index; only reachable via relation
        with repo._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO memory_relations (from_memory_id, to_memory_id, relation, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    "seed",
                    "neighbor",
                    RelationKind.SUPERSEDES.value,
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            conn.commit()
        intent = _intent("seed content", relationship_need=True)
        plan = DeterministicPlanner().plan(_request(), intent, RecallDiagnostics())
        candidates = retriever.retrieve(_request(), plan, RecallDiagnostics())
        ids = {c.memory_id for c in candidates}
        assert "neighbor" in ids


class TestRetrievalDegradation:
    def test_embedding_failure_degrades_semantic(self, repo, index, retriever):
        class _BrokenEmbedder:
            dimension = _DIM

            def embed(self, texts):  # noqa: ARG002
                raise RuntimeError("embedding unavailable")

        broken = MultiLaneCandidateRetriever(
            _BrokenEmbedder(), index, RecallReadRepository(repo.path)
        )
        diag = RecallDiagnostics()
        plan = DeterministicPlanner().plan(_request(), _intent(), diag)
        broken.retrieve(_request(), plan, diag)
        assert DegradationCode.SEMANTIC_UNAVAILABLE in diag.degradations

    def test_index_failure_degrades_vector(self, repo, embedder, retriever):
        class _BrokenIndex:
            def query_candidates(self, **kwargs):  # noqa: ARG002
                raise RuntimeError("chroma down")

            def query(self, **kwargs):  # noqa: ARG002
                raise RuntimeError

        broken = MultiLaneCandidateRetriever(
            embedder, _BrokenIndex(), RecallReadRepository(repo.path)
        )
        diag = RecallDiagnostics()
        record = _record("m1", content="x")
        repo.save_memory(record)
        plan = DeterministicPlanner().plan(_request(), _intent("x"), diag)
        broken.retrieve(_request(), plan, diag)
        assert DegradationCode.VECTOR_INDEX_UNAVAILABLE in diag.degradations

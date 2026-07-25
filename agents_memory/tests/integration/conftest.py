"""Integration fixtures for the Recall pipeline.

The Recall E2E suite drives the real seven-stage pipeline against a real
SQLite repository, but substitutes deterministic fakes for the external
embedder, vector index and LLM so the suite stays fast, offline and
controllable. See design 15 (Fake-driven default tests).
"""

from collections.abc import Callable

import pytest

from agents_memory.embedding.base import FakeEmbedder
from agents_memory.models import MemoryRecord, Validity
from agents_memory.service import MemoryService
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import IndexHit

DEFAULT_INTENT_JSON = (
    '{"primary_query":"recall query","purpose":"general_recovery",'
    '"query_variants":[],"target_memory_types":[],"temporal_need":null,'
    '"subject_hints":[],"relationship_need":false,"confidence":0.9}'
)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class FakeLLMClient:
    """OpenAI-compatible client returning canned JSON, routed by system prompt.

    ``chat`` and ``completions`` return ``self`` so ``client.chat.completions.
    create(...)`` dispatches to :meth:`create`. Tests configure the three
    response bodies (intent / scoring / evidence) and optionally raise.
    """

    def __init__(self) -> None:
        self.intent_response: str = DEFAULT_INTENT_JSON
        self.scoring_response: str = '{"reviews":[]}'
        self.evidence_response: str = '{"groups":[]}'
        self.exception: Exception | None = None
        self.calls: list[str] = []

    @property
    def chat(self) -> "FakeLLMClient":
        return self

    @property
    def completions(self) -> "FakeLLMClient":
        return self

    def create(  # type: ignore[no-untyped-def]
        self,
        *,
        model: str,
        messages,
        response_format=None,
        **kwargs,
    ) -> _FakeLLMResponse:
        system = messages[0]["content"]
        if "analyze a memory recall request" in system:
            kind, content = "intent", self.intent_response
        elif "Review each candidate" in system:
            kind, content = "scoring", self.scoring_response
        elif "Group candidate" in system:
            kind, content = "evidence", self.evidence_response
        else:
            kind, content = "unknown", "{}"
        self.calls.append(kind)
        if self.exception is not None:
            raise self.exception
        return _FakeLLMResponse(content)


class FakeIndex:
    """In-memory MemoryIndex; upsert to seed, query_candidates returns hits."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._embeddings: dict[str, list[float]] = {}
        self.candidates_override: list[IndexHit] | None = None
        self.fail_query: bool = False
        self.query_calls: list[dict] = []

    def upsert(self, record: MemoryRecord, embedding: list[float]) -> None:
        self._records[record.id] = record
        self._embeddings[record.id] = list(embedding)

    def delete(self, memory_id: str) -> None:
        self._records.pop(memory_id, None)
        self._embeddings.pop(memory_id, None)

    def list_ids(self) -> list[str]:
        return list(self._records.keys())

    def query(  # type: ignore[no-untyped-def]
        self, embedding, *, scope, type, top_k, threshold
    ):
        return []

    def query_candidates(  # type: ignore[no-untyped-def]
        self,
        embedding,
        *,
        user_id,
        types,
        top_k,
        agent_id=None,
        session_id=None,
        validities=(Validity.ACTIVE,),
        threshold=0.0,
    ):
        self.query_calls.append(
            {"user_id": user_id, "types": tuple(types), "top_k": top_k}
        )
        if self.fail_query:
            raise RuntimeError("index unavailable")
        if self.candidates_override is not None:
            return list(self.candidates_override)
        hits = []
        for mid, record in self._records.items():
            if record.scope.user_id != user_id:
                continue
            if agent_id is not None and record.scope.agent_id != agent_id:
                continue
            if session_id is not None and record.scope.session_id != session_id:
                continue
            hits.append(IndexHit(memory_id=mid, similarity=0.9))
        return hits[:top_k]


@pytest.fixture
def recall_repository(tmp_path):
    return MemoryRepository(tmp_path / "recall.sqlite")


@pytest.fixture
def recall_read_repository(tmp_path):
    return RecallReadRepository(tmp_path / "recall.sqlite")


@pytest.fixture
def recall_embedder() -> FakeEmbedder:
    return FakeEmbedder(dimension=8)


@pytest.fixture
def recall_index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture
def recall_client() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
def recall_service_factory(
    recall_repository: MemoryRepository,
    recall_read_repository: RecallReadRepository,
    recall_embedder: FakeEmbedder,
    recall_index: FakeIndex,
    recall_client: FakeLLMClient,
) -> Callable[..., MemoryService]:
    """Build a MemoryService wired with the real Recall pipeline + fakes."""

    def factory(
        *,
        client: FakeLLMClient | None = None,
        index: FakeIndex | None = None,
        embedder: FakeEmbedder | None = None,
    ) -> MemoryService:
        from agents_memory.cli import build_recall_pipeline
        from agents_memory.config import Settings

        pipeline = build_recall_pipeline(
            Settings(),
            client=client or recall_client,
            repository=recall_read_repository,
            embedder=embedder or recall_embedder,
            index=index or recall_index,
        )
        return MemoryService(recall_repository, recall_pipeline=pipeline)

    return factory

from pathlib import Path

from agents_memory.extraction.base import FakeFactExtractor
from agents_memory.extraction.llm import ExtractionOutputError
from agents_memory.models import (
    CandidateMemory,
    MemoryScope,
    MemoryType,
    Message,
    RelationKind,
    RelationMatch,
    SourceKind,
    Validity,
    WriteStatus,
)
from agents_memory.pipeline.write import MemoryWritePipeline
from agents_memory.processing.candidate import CandidateProcessor
from agents_memory.processing.decision import DecisionEngine
from agents_memory.processing.decision import AmbiguousDecision
from agents_memory.resolution.llm import RelationOutputError
from agents_memory.retrieval.lookup import HistoricalMatch, LookupResult
from agents_memory.retrieval.lookup import IndexLookupError
from agents_memory.storage.coordinator import StorageCoordinator
from agents_memory.storage.repository import MemoryRepository


class InMemoryIndex:
    def __init__(self):
        self.items = {}

    def upsert(self, record, embedding):
        self.items[record.id] = (record, embedding)

    def delete(self, memory_id):
        self.items.pop(memory_id, None)

    def clear(self):
        self.items.clear()


class StubEmbedder:
    dimension = 2

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class RepositoryLookup:
    def __init__(self, repository):
        self.repository = repository

    def lookup(self, content, scope, type):
        matches = tuple(
            HistoricalMatch(record=item, similarity=0.9)
            for item in self.repository.list_memories(scope, type)
        )
        return LookupResult(embedding=[1.0, 0.0], matches=matches)


class ContentResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, candidate, histories):
        self.calls += 1
        relations = []
        for item in histories:
            if item.content == candidate.content or (
                "Python" in item.content and "Python" in candidate.content
            ):
                kind = RelationKind.DUPLICATE
            elif "北京" in candidate.content and "上海" in item.content:
                kind = RelationKind.CONTRADICT
            else:
                kind = RelationKind.SUPPLEMENT
            relations.append(RelationMatch(memory_id=item.id, relation=kind))
        return relations


def candidate(content: str) -> CandidateMemory:
    return CandidateMemory(
        content=content,
        type=MemoryType.FACT,
        importance=8,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
    )


def pipeline(tmp_path: Path, candidates: list[CandidateMemory]):
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    index = InMemoryIndex()
    extractor = FakeFactExtractor(candidates)
    resolver = ContentResolver()
    coordinator = StorageCoordinator(repository, index, StubEmbedder())
    subject = MemoryWritePipeline(
        extractor=extractor,
        processor=CandidateProcessor(),
        lookup=RepositoryLookup(repository),
        resolver=resolver,
        decision_engine=DecisionEngine(),
        coordinator=coordinator,
        repository=repository,
    )
    return subject, repository, extractor


def write(subject, request_id="req-1"):
    return subject.write(
        request_id=request_id,
        scope=MemoryScope(user_id="u1"),
        messages=[Message(message_id="m1", role="user", content="message")],
    )


def test_pipeline_adds_and_idempotently_returns_saved_report(tmp_path: Path) -> None:
    subject, repository, extractor = pipeline(tmp_path, [candidate("用户偏好 Python")])

    first = write(subject)
    second = write(subject)

    assert first.status is WriteStatus.SUCCESS
    assert second == first
    assert extractor.calls == 1
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 1


def test_pipeline_noops_duplicate(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(tmp_path, [candidate("用户偏好 Python")])
    write(subject, "req-1")

    subject.extractor = FakeFactExtractor([candidate("用户偏好 Python")])
    report = write(subject, "req-2")

    assert report.results[0].action.value == "noop"
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 1


def test_pipeline_updates_current_fact_and_preserves_history(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(tmp_path, [candidate("用户住在上海")])
    first = write(subject, "req-1")

    subject.extractor = FakeFactExtractor([candidate("用户搬到北京")])
    second = write(subject, "req-2")

    assert second.results[0].action.value == "update"
    assert second.results[0].matches[0].memory_id == first.results[0].memory_id
    assert second.results[0].matches[0].relation is RelationKind.CONTRADICT
    assert second.results[0].matches[0].similarity == 0.9
    old = repository.get_memory(first.results[0].memory_id)
    assert old is not None and old.validity is Validity.SUPERSEDED
    assert len(
        repository.list_memories(MemoryScope(user_id="u1"), include_history=True)
    ) == 2


def test_pipeline_handles_zero_candidates_as_success(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(tmp_path, [])

    report = write(subject)

    assert report.status is WriteStatus.SUCCESS
    assert report.results == ()
    assert repository.list_memories(MemoryScope(user_id="u1")) == []


def test_pipeline_uses_pending_batch_results_for_semantic_dedup(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(
        tmp_path,
        [candidate("用户偏好 Python"), candidate("用户喜欢 Python")],
    )

    report = write(subject)

    assert [result.action.value for result in report.results] == ["add", "noop"]
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 1


def test_pipeline_pending_candidates_do_not_cross_memory_type(tmp_path: Path) -> None:
    first = candidate("用户偏好 Python")
    second = candidate("用户偏好 Python").model_copy(update={"type": MemoryType.EVENT})
    subject, repository, _ = pipeline(tmp_path, [first, second])

    report = write(subject)

    assert [result.action.value for result in report.results] == ["add", "add"]
    assert len(
        repository.list_memories(
            MemoryScope(user_id="u1"), include_history=True
        )
    ) == 2


def test_pipeline_returns_retryable_report_when_lookup_is_unavailable(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(tmp_path, [candidate("用户偏好 Python")])

    class FailedLookup:
        def lookup(self, *_args, **_kwargs):
            raise IndexLookupError("offline")

    subject.lookup = FailedLookup()
    report = write(subject)

    assert report.status is WriteStatus.RETRYABLE
    assert not report.sqlite_committed
    assert report.error_code.value == "index_unavailable"
    assert repository.list_memories(MemoryScope(user_id="u1")) == []


def test_pipeline_classifies_precommit_failures_without_persisting(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(tmp_path, [candidate("用户偏好 Python")])

    class FailedExtractor:
        def extract(self, _messages):
            raise ExtractionOutputError("bad output")

    subject.extractor = FailedExtractor()
    extraction = write(subject, "req-extract")
    assert extraction.status is WriteStatus.FAILED
    assert extraction.error_code.value == "extraction_failed"

    subject.extractor = FakeFactExtractor([candidate("用户偏好 Python")])

    class FailedResolver:
        def resolve(self, *_args):
            raise RelationOutputError("bad relation")

    subject.resolver = FailedResolver()
    relation = write(subject, "req-relation")
    assert relation.status is WriteStatus.FAILED
    assert relation.error_code.value == "relation_failed"

    subject.resolver = ContentResolver()

    class AmbiguousEngine:
        def decide(self, *_args):
            raise AmbiguousDecision("mixed")

    subject.decision_engine = AmbiguousEngine()
    ambiguous = write(subject, "req-ambiguous")
    assert ambiguous.status is WriteStatus.FAILED
    assert ambiguous.error_code.value == "ambiguous_relation"

    subject.decision_engine = DecisionEngine()

    class FailedCoordinator:
        def commit(self, **_kwargs):
            raise RuntimeError("sqlite failure")

    subject.coordinator = FailedCoordinator()
    storage = write(subject, "req-storage")
    assert storage.status is WriteStatus.FAILED
    assert storage.error_code.value == "storage_failed"
    assert repository.list_memories(MemoryScope(user_id="u1")) == []


def test_pipeline_rejects_idempotency_key_with_different_input(tmp_path: Path) -> None:
    subject, repository, _ = pipeline(tmp_path, [candidate("用户偏好 Python")])
    write(subject, "req-1")

    report = subject.write(
        request_id="req-1",
        scope=MemoryScope(user_id="u1"),
        messages=[Message(message_id="m2", role="user", content="different")],
    )

    assert report.status is WriteStatus.FAILED
    assert report.error_code.value == "idempotency_conflict"
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 1

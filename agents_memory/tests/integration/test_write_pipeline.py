from pathlib import Path

import pytest

from agents_memory.models import EventFrame, EventIdentity, EventStatus, TemporalRelation
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
from agents_memory.storage.coordinator import RequestAlreadyReserved, StorageCoordinator
from agents_memory.storage.repository import MemoryRepository, StaleMemoryState


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


def event_candidate(content: str, status: EventStatus) -> CandidateMemory:
    return candidate(content).model_copy(
        update={
            "type": MemoryType.EVENT,
            "event_frame": EventFrame(
                actor="user",
                predicate="travel",
                object="北京",
                status=status,
            ),
        }
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


def write_message(subject, request_id: str, message_id: str, content: str):
    return subject.write(
        request_id=request_id,
        scope=MemoryScope(user_id="u1"),
        messages=[Message(message_id=message_id, role="user", content=content)],
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


def test_pipeline_defers_unknown_event_conflict_without_polluting_memory(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户计划去北京", EventStatus.PLANNED)]
    )
    first = write(subject, "req-event-1")

    class UnknownEventResolver:
        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=EventIdentity.UNKNOWN,
                    temporal=TemporalRelation.UNKNOWN,
                    reason="缺少事件时间",
                )
                for item in histories
            ]

    subject.resolver = UnknownEventResolver()
    subject.extractor = FakeFactExtractor(
        [event_candidate("用户不去北京了", EventStatus.CANCELLED)]
    )
    deferred = write(subject, "req-event-2")

    assert deferred.status is WriteStatus.SUCCESS
    assert deferred.results[0].action.value == "defer"
    assert deferred.results[0].resolution_id is not None
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 1
    assert repository.list_memories(MemoryScope(user_id="u1"))[0].id == (
        first.results[0].memory_id
    )
    assert len(repository.list_pending_resolutions(MemoryScope(user_id="u1"))) == 1


def test_pipeline_commits_unrelated_fact_alongside_deferred_event(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户计划去北京", EventStatus.PLANNED)]
    )
    write(subject, "req-event-1")

    class MixedResolver:
        def resolve(self, current, histories):
            if current.type is MemoryType.EVENT:
                return [
                    RelationMatch(
                        memory_id=item.id,
                        relation=RelationKind.CONTRADICT,
                        identity=EventIdentity.UNKNOWN,
                        temporal=TemporalRelation.UNKNOWN,
                    )
                    for item in histories
                ]
            return [
                RelationMatch(memory_id=item.id, relation=RelationKind.NONE)
                for item in histories
            ]

    subject.resolver = MixedResolver()
    subject.extractor = FakeFactExtractor(
        [
            event_candidate("用户不去北京了", EventStatus.CANCELLED),
            candidate("用户偏好 Python"),
        ]
    )
    report = write(subject, "req-mixed")

    assert [item.action.value for item in report.results] == ["defer", "add"]
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 2


def test_pipeline_silently_resolves_defer_on_later_write(tmp_path: Path) -> None:
    planned = event_candidate("用户计划去北京", EventStatus.PLANNED).model_copy(
        update={"source_message_ids": ("planned",)}
    )
    subject, repository, _ = pipeline(tmp_path, [planned])
    first = write_message(subject, "req-plan", "planned", "我计划去北京")

    class UnknownThenSameResolver:
        def __init__(self):
            self.identity = EventIdentity.UNKNOWN

        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=self.identity,
                    temporal=TemporalRelation.SAME_WINDOW,
                )
                for item in histories
            ]

    resolver = UnknownThenSameResolver()
    subject.resolver = resolver
    cancelled = event_candidate(
        "用户不去北京了", EventStatus.CANCELLED
    ).model_copy(update={"source_message_ids": ("ambiguous",)})
    subject.extractor = FakeFactExtractor([cancelled])
    deferred = write_message(subject, "req-defer", "ambiguous", "不去了")
    resolution_id = deferred.results[0].resolution_id
    assert resolution_id is not None

    resolver.identity = EventIdentity.SAME_EVENT
    confirmed = event_candidate(
        "用户已取消北京行程", EventStatus.CANCELLED
    ).model_copy(update={"source_message_ids": ("evidence",)})
    subject.extractor = FakeFactExtractor([confirmed])
    resolved = write_message(
        subject, "req-resolve", "evidence", "北京机票已经退好了"
    )

    assert [item.action.value for item in resolved.results] == ["update"]
    assert (
        repository.get_pending_resolution(resolution_id).status.value  # type: ignore[union-attr]
        == "resolved"
    )
    old = repository.get_memory(first.results[0].memory_id)
    assert old is not None and old.validity is Validity.SUPERSEDED
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 1
    current = repository.list_memories(MemoryScope(user_id="u1"))[0]
    assert {source.message_id for source in repository.get_sources(current.id)} == {
        "ambiguous",
        "evidence",
    }


def test_pipeline_groups_related_deferred_candidates_by_conflict_target(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户计划去北京", EventStatus.PLANNED)]
    )
    write(subject, "req-plan")

    class UnknownResolver:
        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=EventIdentity.UNKNOWN,
                    temporal=TemporalRelation.UNKNOWN,
                )
                for item in histories
            ]

    subject.resolver = UnknownResolver()
    subject.extractor = FakeFactExtractor(
        [
            event_candidate("用户不去北京了", EventStatus.CANCELLED),
            event_candidate("北京行程可能取消", EventStatus.CANCELLED),
        ]
    )

    report = write(subject, "req-group")

    assert [result.action.value for result in report.results] == ["defer", "defer"]
    assert report.results[0].resolution_id == report.results[1].resolution_id
    stored = repository.list_pending_resolutions(MemoryScope(user_id="u1"))
    assert len(stored) == 1
    assert len(stored[0].grouped_candidates) == 2


def test_pipeline_reuses_pending_for_later_still_ambiguous_evidence(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户计划去北京", EventStatus.PLANNED)]
    )
    write(subject, "req-plan")

    class UnknownResolver:
        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=EventIdentity.UNKNOWN,
                    temporal=TemporalRelation.UNKNOWN,
                )
                for item in histories
            ]

    subject.resolver = UnknownResolver()
    first_candidate = event_candidate(
        "用户不去北京了", EventStatus.CANCELLED
    ).model_copy(update={"source_message_ids": ("ambiguous-1",)})
    subject.extractor = FakeFactExtractor([first_candidate])
    first = write_message(
        subject, "req-defer-1", "ambiguous-1", "后来不去了"
    )

    second_candidate = event_candidate(
        "用户可能取消了那次行程", EventStatus.CANCELLED
    ).model_copy(update={"source_message_ids": ("ambiguous-2",)})
    subject.extractor = FakeFactExtractor([second_candidate])
    second = write_message(
        subject, "req-defer-2", "ambiguous-2", "那次行程可能取消了"
    )

    assert first.results[0].resolution_id == second.results[0].resolution_id
    assert len(repository.list_pending_resolutions(MemoryScope(user_id="u1"))) == 1


def test_pipeline_adds_different_event_without_replacing_history(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户去年去了北京", EventStatus.COMPLETED)]
    )
    first = write(subject, "req-first-event")

    class DifferentEventResolver:
        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=EventIdentity.DIFFERENT_EVENT,
                    temporal=TemporalRelation.AFTER,
                )
                for item in histories
            ]

    subject.resolver = DifferentEventResolver()
    subject.extractor = FakeFactExtractor(
        [event_candidate("用户今年去了北京", EventStatus.COMPLETED)]
    )
    second = write(subject, "req-second-event")

    assert second.results[0].action.value == "add"
    assert repository.get_memory(first.results[0].memory_id).validity is Validity.ACTIVE  # type: ignore[union-attr]
    assert len(repository.list_memories(MemoryScope(user_id="u1"))) == 2


def test_pipeline_retracts_same_event_on_explicit_correction(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户明天去北京", EventStatus.PLANNED)]
    )
    first = write(subject, "req-wrong-event")

    class CorrectionResolver:
        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CORRECT,
                    identity=EventIdentity.SAME_EVENT,
                    temporal=TemporalRelation.SAME_WINDOW,
                    explicit_correction=True,
                )
                for item in histories
            ]

    subject.resolver = CorrectionResolver()
    subject.extractor = FakeFactExtractor(
        [event_candidate("用户澄清明天去上海", EventStatus.PLANNED)]
    )
    corrected = write(subject, "req-correct-event")

    assert corrected.results[0].action.value == "update"
    assert repository.get_memory(first.results[0].memory_id).validity is Validity.RETRACTED  # type: ignore[union-attr]


def test_pipeline_does_not_reconcile_pending_across_scope(
    tmp_path: Path,
) -> None:
    subject, repository, _ = pipeline(
        tmp_path, [event_candidate("用户计划去北京", EventStatus.PLANNED)]
    )
    write(subject, "req-plan")

    class CountingUnknownResolver:
        def __init__(self):
            self.calls = 0

        def resolve(self, _candidate, histories):
            self.calls += 1
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=EventIdentity.UNKNOWN,
                    temporal=TemporalRelation.UNKNOWN,
                )
                for item in histories
            ]

    resolver = CountingUnknownResolver()
    subject.resolver = resolver
    subject.extractor = FakeFactExtractor(
        [
            event_candidate(
                "用户不去北京了", EventStatus.CANCELLED
            ).model_copy(update={"source_message_ids": ("defer",)})
        ]
    )
    write_message(subject, "req-defer", "defer", "不去了")
    calls_after_defer = resolver.calls
    subject.extractor = FakeFactExtractor([])

    report = subject.write(
        request_id="req-other-scope",
        scope=MemoryScope(user_id="u2"),
        messages=[
            Message(message_id="other-message", role="user", content="就是那次")
        ],
    )

    assert report.results == ()
    assert resolver.calls == calls_after_defer
    assert len(repository.list_pending_resolutions(MemoryScope(user_id="u1"))) == 1


def test_pipeline_reports_reconciliation_relation_failure(
    tmp_path: Path,
) -> None:
    subject, _, _ = pipeline(
        tmp_path, [event_candidate("用户计划去北京", EventStatus.PLANNED)]
    )
    planned = event_candidate(
        "用户计划去北京", EventStatus.PLANNED
    ).model_copy(update={"source_message_ids": ("plan",)})
    subject.extractor = FakeFactExtractor([planned])
    write_message(subject, "req-plan", "plan", "我计划去北京")

    class UnknownResolver:
        def resolve(self, _candidate, histories):
            return [
                RelationMatch(
                    memory_id=item.id,
                    relation=RelationKind.CONTRADICT,
                    identity=EventIdentity.UNKNOWN,
                    temporal=TemporalRelation.UNKNOWN,
                )
                for item in histories
            ]

    subject.resolver = UnknownResolver()
    subject.extractor = FakeFactExtractor(
        [
            event_candidate(
                "用户不去北京了", EventStatus.CANCELLED
            ).model_copy(update={"source_message_ids": ("defer",)})
        ]
    )
    write_message(subject, "req-defer", "defer", "不去了")

    class FailedResolver:
        def resolve(self, *_args):
            raise RelationOutputError("bad reconciliation")

    subject.resolver = FailedResolver()
    subject.extractor = FakeFactExtractor(
        [
            event_candidate(
                "北京行程是明天", EventStatus.PLANNED
            ).model_copy(update={"source_message_ids": ("evidence",)})
        ]
    )

    report = write_message(
        subject, "req-reconcile-fail", "evidence", "北京行程是明天"
    )

    assert report.status is WriteStatus.FAILED
    assert report.error_code.value == "relation_failed"


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


@pytest.mark.parametrize(
    "error",
    [RequestAlreadyReserved("busy"), StaleMemoryState("stale")],
)
def test_pipeline_classifies_concurrent_commit_as_retryable(
    tmp_path: Path,
    error: Exception,
) -> None:
    subject, repository, _ = pipeline(tmp_path, [candidate("用户偏好 Python")])

    class ConcurrentCoordinator:
        def commit(self, **_kwargs):
            raise error

    subject.coordinator = ConcurrentCoordinator()
    report = write(subject, f"req-{type(error).__name__}")

    assert report.status is WriteStatus.RETRYABLE
    assert report.retryable
    assert report.error_code.value == "request_in_progress"
    assert repository.list_memories(MemoryScope(user_id="u1")) == []

import hashlib
import json
from uuid import uuid4

from agents_memory.extraction.base import FactExtractor
from agents_memory.extraction.llm import ExtractionOutputError, SourceAttributionError
from agents_memory.models import (
    Action,
    ErrorCode,
    MemoryRecord,
    MemoryScope,
    Message,
    Validity,
    WriteReport,
    WriteStatus,
)
from agents_memory.processing.candidate import CandidateProcessor
from agents_memory.processing.decision import AmbiguousDecision, DecisionEngine
from agents_memory.resolution.base import RelationResolver
from agents_memory.resolution.llm import RelationOutputError
from agents_memory.retrieval.lookup import ContextLookup, IndexLookupError
from agents_memory.storage.coordinator import RequestAlreadyReserved, StorageCoordinator
from agents_memory.storage.repository import IdempotencyConflict, MemoryRepository


class MemoryWritePipeline:
    def __init__(
        self,
        *,
        extractor: FactExtractor,
        processor: CandidateProcessor,
        lookup: ContextLookup,
        resolver: RelationResolver,
        decision_engine: DecisionEngine,
        coordinator: StorageCoordinator,
        repository: MemoryRepository,
    ) -> None:
        self.extractor = extractor
        self.processor = processor
        self.lookup = lookup
        self.resolver = resolver
        self.decision_engine = decision_engine
        self.coordinator = coordinator
        self.repository = repository

    def write(
        self,
        *,
        request_id: str,
        scope: MemoryScope,
        messages: list[Message],
    ) -> WriteReport:
        input_hash = self._input_hash(scope, messages)
        try:
            stored = self.repository.get_request(request_id, input_hash)
        except IdempotencyConflict as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.FAILED,
                error_code=ErrorCode.IDEMPOTENCY_CONFLICT,
                error_message=str(exc),
            )
        if stored is not None and stored.report is not None:
            if stored.status == "complete":
                return stored.report
            if stored.status == "committed":
                return self.coordinator.repair_request(request_id, input_hash)

        try:
            extracted = self.extractor.extract(messages)
            batch = self.processor.process(extracted, messages)
        except (ExtractionOutputError, SourceAttributionError, ValueError) as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.FAILED,
                error_code=ErrorCode.EXTRACTION_FAILED,
                error_message=str(exc),
            )
        plans = []
        embeddings: dict[int, list[float]] = {}
        pending: list[MemoryRecord] = []

        for index, candidate in enumerate(batch.candidates):
            try:
                lookup_result = self.lookup.lookup(candidate.content, scope, candidate.type)
            except IndexLookupError as exc:
                return WriteReport(
                    request_id=request_id,
                    status=WriteStatus.RETRYABLE,
                    extracted_count=len(extracted),
                    filtered_count=batch.filtered_count,
                    retryable=True,
                    error_code=ErrorCode.INDEX_UNAVAILABLE,
                    error_message=str(exc),
                )
            histories = [item.record for item in lookup_result.matches] + [
                item for item in pending if item.type is candidate.type
            ]
            try:
                relations = self.resolver.resolve(candidate, histories)
                similarities = {
                    item.record.id: item.similarity for item in lookup_result.matches
                }
                relations = [
                    relation.model_copy(
                        update={"similarity": similarities.get(relation.memory_id)}
                    )
                    for relation in relations
                ]
                plan = self.decision_engine.decide(index, candidate, histories, relations)
            except RelationOutputError as exc:
                return WriteReport(
                    request_id=request_id,
                    status=WriteStatus.FAILED,
                    extracted_count=len(extracted),
                    filtered_count=batch.filtered_count,
                    error_code=ErrorCode.RELATION_FAILED,
                    error_message=str(exc),
                )
            except AmbiguousDecision as exc:
                return WriteReport(
                    request_id=request_id,
                    status=WriteStatus.FAILED,
                    extracted_count=len(extracted),
                    filtered_count=batch.filtered_count,
                    error_code=ErrorCode.AMBIGUOUS_RELATION,
                    error_message=str(exc),
                )
            if plan.action in (Action.ADD, Action.UPDATE):
                if plan.action is Action.UPDATE:
                    pending = [
                        item for item in pending if item.id not in plan.target_ids
                    ]
                memory_id = str(uuid4())
                plan = plan.model_copy(update={"new_memory_id": memory_id})
                pending.append(
                    MemoryRecord(
                        id=memory_id,
                        scope=scope,
                        type=candidate.type,
                        content=candidate.content,
                        importance=candidate.importance,
                        confidence=candidate.confidence,
                        validity=Validity.ACTIVE,
                        metadata=candidate.metadata,
                    )
                )
            plans.append(plan)
            embeddings[index] = lookup_result.embedding

        try:
            return self.coordinator.commit(
                request_id=request_id,
                input_hash=input_hash,
                scope=scope,
                plans=plans,
                messages=messages,
                embeddings=embeddings,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
            )
        except RequestAlreadyReserved as exc:
            current = self.repository.get_request(request_id, input_hash)
            if current is not None and current.report is not None:
                return current.report
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.RETRYABLE,
                retryable=True,
                error_code=ErrorCode.REQUEST_IN_PROGRESS,
                error_message=str(exc),
            )
        except Exception as exc:
            current = self.repository.get_request(request_id, input_hash)
            if current is not None and current.report is not None:
                return current.report
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.FAILED,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
                error_code=ErrorCode.STORAGE_FAILED,
                error_message=str(exc),
            )

    @staticmethod
    def _input_hash(scope: MemoryScope, messages: list[Message]) -> str:
        payload = {
            "scope": scope.model_dump(mode="json"),
            "messages": [message.model_dump(mode="json") for message in messages],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

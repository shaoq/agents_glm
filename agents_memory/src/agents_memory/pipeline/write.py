import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from agents_memory.extraction.base import FactExtractor
from agents_memory.extraction.llm import ExtractionOutputError, SourceAttributionError
from agents_memory.models import (
    Action,
    ErrorCode,
    MemoryRecord,
    MemoryScope,
    Message,
    PendingResolution,
    PendingResolutionStatus,
    Validity,
    WriteReport,
    WriteStatus,
)
from agents_memory.processing.candidate import CandidateProcessor
from agents_memory.processing.decision import AmbiguousDecision, DecisionEngine
from agents_memory.processing.pending import (
    PendingResolutionPolicy,
    missing_event_dimensions,
)
from agents_memory.processing.reconciliation import PendingResolutionReconciler
from agents_memory.resolution.base import RelationResolver
from agents_memory.resolution.llm import RelationOutputError
from agents_memory.retrieval.lookup import ContextLookup, IndexLookupError
from agents_memory.storage.coordinator import RequestAlreadyReserved, StorageCoordinator
from agents_memory.storage.repository import (
    IdempotencyConflict,
    MemoryRepository,
    StaleMemoryState,
)


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
        pending_policy: PendingResolutionPolicy | None = None,
        reconciler: PendingResolutionReconciler | None = None,
    ) -> None:
        self.extractor = extractor
        self.processor = processor
        self.lookup = lookup
        self.resolver = resolver
        self.decision_engine = decision_engine
        self.coordinator = coordinator
        self.repository = repository
        self.pending_policy = pending_policy or PendingResolutionPolicy()
        self.reconciler = reconciler or PendingResolutionReconciler(
            repository, decision_engine
        )

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
        except (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        ) as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.RETRYABLE,
                retryable=True,
                error_code=ErrorCode.EXTRACTION_FAILED,
                error_message=str(exc),
            )
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
        inactive_ids: set[str] = set()
        deferred_groups: list[PendingResolution] = []
        try:
            reconciliation = self.reconciler.reconcile(
                scope=scope,
                messages=messages,
                candidates=batch.candidates,
                resolver=self.resolver,
            )
        except RelationOutputError as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.FAILED,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
                error_code=ErrorCode.RELATION_FAILED,
                error_message=str(exc),
            )
        except (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        ) as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.RETRYABLE,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
                retryable=True,
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
        except ValueError as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.FAILED,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
                error_code=ErrorCode.INVALID_INPUT,
                error_message=str(exc),
            )
        for resolution_plan in reconciliation.plans:
            plan = resolution_plan
            if plan.action in (Action.ADD, Action.UPDATE):
                try:
                    resolution_lookup = self.lookup.lookup(
                        plan.candidate.content, scope, plan.candidate.type
                    )
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
                embeddings[plan.candidate_index] = resolution_lookup.embedding
                if plan.action is Action.UPDATE:
                    inactive_ids.update(plan.target_ids)
                    pending = [
                        item for item in pending if item.id not in plan.target_ids
                    ]
                memory_id = str(uuid4())
                plan = plan.model_copy(update={"new_memory_id": memory_id})
                pending.append(
                    MemoryRecord(
                        id=memory_id,
                        scope=scope,
                        type=plan.candidate.type,
                        content=plan.candidate.content,
                        importance=plan.candidate.importance,
                        confidence=plan.candidate.confidence,
                        validity=Validity.ACTIVE,
                        event_frame=plan.candidate.event_frame,
                        metadata=plan.candidate.metadata,
                    )
                )
            plans.append(plan)

        for index, candidate in enumerate(batch.candidates):
            if index in reconciliation.consumed_candidate_indexes:
                continue
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
            histories = [
                item.record
                for item in lookup_result.matches
                if item.record.id not in inactive_ids
            ] + [
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
            except (
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            ) as exc:
                return WriteReport(
                    request_id=request_id,
                    status=WriteStatus.RETRYABLE,
                    extracted_count=len(extracted),
                    filtered_count=batch.filtered_count,
                    retryable=True,
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
                    inactive_ids.update(plan.target_ids)
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
                        event_frame=candidate.event_frame,
                        metadata=candidate.metadata,
                    )
                )
            elif plan.action is Action.DEFER:
                now = datetime.now(UTC)
                existing_group = next(
                    (
                        group
                        for group in deferred_groups
                        if set(group.conflicting_memory_ids) & set(plan.target_ids)
                        or self.reconciler._group_frames_related(
                            group.grouped_candidates
                            or (group.candidate,),
                            candidate,
                        )
                    ),
                    None,
                )
                if existing_group is None:
                    pending_resolution = PendingResolution(
                        id=str(uuid4()),
                        scope=scope,
                        candidate=candidate,
                        grouped_candidates=(candidate,),
                        conflicting_memory_ids=plan.target_ids,
                        semantic_relation=plan.relation
                        or next(
                            (
                                item.relation
                                for item in plan.matches
                                if item.relation.value != "none"
                            ),
                            None,
                        )
                        or plan.matches[0].relation,
                        missing_dimensions=missing_event_dimensions(candidate),
                        reason=plan.reason,
                        source_message_ids=candidate.source_message_ids,
                        source_messages=tuple(
                            message
                            for message in messages
                            if message.message_id
                            in candidate.source_message_ids
                        ),
                        processed_evidence_message_ids=candidate.source_message_ids,
                        importance=candidate.importance,
                        status=PendingResolutionStatus.OPEN,
                        created_at=now,
                        updated_at=now,
                        last_evaluated_at=now,
                        expires_at=self.pending_policy.expires_at(
                            candidate.importance, now=now
                        ),
                    )
                    deferred_groups.append(pending_resolution)
                else:
                    source_ids = tuple(
                        dict.fromkeys(
                            (
                                *existing_group.source_message_ids,
                                *candidate.source_message_ids,
                            )
                        )
                    )
                    pending_resolution = existing_group.model_copy(
                        update={
                            "grouped_candidates": (
                                *existing_group.grouped_candidates,
                                candidate,
                            ),
                            "conflicting_memory_ids": tuple(
                                dict.fromkeys(
                                    (
                                        *existing_group.conflicting_memory_ids,
                                        *plan.target_ids,
                                    )
                                )
                            ),
                            "source_message_ids": source_ids,
                            "source_messages": tuple(
                                {
                                    message.message_id: message
                                    for message in (
                                        *existing_group.source_messages,
                                        *messages,
                                    )
                                    if message.message_id in source_ids
                                }.values()
                            ),
                            "processed_evidence_message_ids": tuple(
                                dict.fromkeys(
                                    (
                                        *existing_group.processed_evidence_message_ids,
                                        *candidate.source_message_ids,
                                    )
                                )
                            ),
                            "importance": max(
                                existing_group.importance,
                                candidate.importance,
                            ),
                            "updated_at": now,
                        }
                    )
                    deferred_groups[
                        deferred_groups.index(existing_group)
                    ] = pending_resolution
                plan = plan.model_copy(
                    update={"pending_resolution": pending_resolution}
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
        except StaleMemoryState as exc:
            return WriteReport(
                request_id=request_id,
                status=WriteStatus.RETRYABLE,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
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

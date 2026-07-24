import hashlib
import json
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
    CandidateMemory,
    ErrorCode,
    MemoryScope,
    Message,
    WriteReport,
    WriteStatus,
)
from agents_memory.pipeline.state import WriteBatchState
from agents_memory.processing.candidate import CandidateBatch, CandidateProcessor
from agents_memory.processing.decision import AmbiguousDecision, DecisionEngine
from agents_memory.processing.deferred import DeferredResolutionCollector
from agents_memory.processing.pending import PendingResolutionPolicy
from agents_memory.processing.reconciliation import (
    PendingResolutionReconciler,
    ReconciliationResult,
)
from agents_memory.resolution.base import RelationResolver
from agents_memory.resolution.llm import RelationOutputError
from agents_memory.retrieval.lookup import ContextLookup, IndexLookupError
from agents_memory.storage.coordinator import RequestAlreadyReserved, StorageCoordinator
from agents_memory.storage.repository import (
    IdempotencyConflict,
    MemoryRepository,
    StaleMemoryState,
)


class _WriteAborted(Exception):
    """Carry a fully classified report across internal pipeline stages."""

    def __init__(self, report: WriteReport) -> None:
        self.report = report


class MemoryWritePipeline:
    """Coordinate write stages without owning their domain algorithms.

    Planning happens against an in-memory overlay. Only after every candidate
    has a deterministic plan does StorageCoordinator open the SQLite
    transaction and synchronize the derivative vector index.
    """

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
        self.deferred_collector = DeferredResolutionCollector(self.pending_policy)
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
        """Run the request through ordered, fail-fast write stages."""

        input_hash = self._input_hash(scope, messages)
        try:
            existing = self._existing_request(request_id, input_hash)
            if existing is not None:
                return existing

            extracted, batch = self._extract_batch(
                request_id=request_id,
                messages=messages,
            )
            reconciliation = self._reconcile_pending(
                request_id=request_id,
                scope=scope,
                messages=messages,
                batch=batch,
                extracted_count=len(extracted),
            )
            state = WriteBatchState()
            self._apply_reconciliation_plans(
                request_id=request_id,
                scope=scope,
                reconciliation=reconciliation,
                state=state,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
            )
            self._plan_candidates(
                request_id=request_id,
                scope=scope,
                messages=messages,
                batch=batch,
                reconciliation=reconciliation,
                state=state,
                extracted_count=len(extracted),
            )
            return self._commit(
                request_id=request_id,
                input_hash=input_hash,
                scope=scope,
                messages=messages,
                state=state,
                extracted_count=len(extracted),
                filtered_count=batch.filtered_count,
            )
        except _WriteAborted as aborted:
            return aborted.report

    def _existing_request(
        self,
        request_id: str,
        input_hash: str,
    ) -> WriteReport | None:
        """Return a saved result or resume the index half of a committed write."""

        try:
            stored = self.repository.get_request(request_id, input_hash)
        except IdempotencyConflict as exc:
            raise _WriteAborted(
                self._failure(
                    request_id,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    exc,
                )
            ) from exc
        if stored is None or stored.report is None:
            return None
        if stored.status == "complete":
            return stored.report
        if stored.status == "committed":
            return self.coordinator.repair_request(request_id, input_hash)
        return None

    def _extract_batch(
        self,
        *,
        request_id: str,
        messages: list[Message],
    ) -> tuple[list[CandidateMemory], CandidateBatch]:
        """Extract and normalize candidates before any relation or storage work."""

        try:
            extracted = self.extractor.extract(messages)
            return extracted, self.processor.process(extracted, messages)
        except (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        ) as exc:
            raise _WriteAborted(
                self._failure(
                    request_id,
                    ErrorCode.EXTRACTION_FAILED,
                    exc,
                    retryable=True,
                )
            ) from exc
        except (ExtractionOutputError, SourceAttributionError, ValueError) as exc:
            raise _WriteAborted(
                self._failure(request_id, ErrorCode.EXTRACTION_FAILED, exc)
            ) from exc

    def _reconcile_pending(
        self,
        *,
        request_id: str,
        scope: MemoryScope,
        messages: list[Message],
        batch: CandidateBatch,
        extracted_count: int,
    ) -> ReconciliationResult:
        """Use only new natural evidence to reconsider open pending assertions."""

        try:
            return self.reconciler.reconcile(
                scope=scope,
                messages=messages,
                candidates=list(batch.candidates),
                resolver=self.resolver,
            )
        except RelationOutputError as exc:
            raise self._relation_abort(
                request_id, exc, extracted_count, batch.filtered_count
            ) from exc
        except (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        ) as exc:
            raise self._relation_abort(
                request_id,
                exc,
                extracted_count,
                batch.filtered_count,
                retryable=True,
            ) from exc
        except AmbiguousDecision as exc:
            raise _WriteAborted(
                self._failure(
                    request_id,
                    ErrorCode.AMBIGUOUS_RELATION,
                    exc,
                    extracted_count=extracted_count,
                    filtered_count=batch.filtered_count,
                )
            ) from exc
        except ValueError as exc:
            raise _WriteAborted(
                self._failure(
                    request_id,
                    ErrorCode.INVALID_INPUT,
                    exc,
                    extracted_count=extracted_count,
                    filtered_count=batch.filtered_count,
                )
            ) from exc

    def _apply_reconciliation_plans(
        self,
        *,
        request_id: str,
        scope: MemoryScope,
        reconciliation: ReconciliationResult,
        state: WriteBatchState,
        extracted_count: int,
        filtered_count: int,
    ) -> None:
        """Overlay plans that resolve older pending assertions before new work."""

        for resolution_plan in reconciliation.plans:
            plan = resolution_plan
            if plan.action in (Action.ADD, Action.UPDATE):
                try:
                    resolution_lookup = self.lookup.lookup(
                        plan.candidate.content, scope, plan.candidate.type
                    )
                except IndexLookupError as exc:
                    raise _WriteAborted(
                        self._failure(
                            request_id,
                            ErrorCode.INDEX_UNAVAILABLE,
                            exc,
                            retryable=True,
                            extracted_count=extracted_count,
                            filtered_count=filtered_count,
                        )
                    ) from exc
                plan = state.stage_materialized(
                    plan,
                    scope=scope,
                    memory_id=str(uuid4()),
                )
                state.record(plan, embedding=resolution_lookup.embedding)
                continue
            state.record(plan)

    def _plan_candidates(
        self,
        *,
        request_id: str,
        scope: MemoryScope,
        messages: list[Message],
        batch: CandidateBatch,
        reconciliation: ReconciliationResult,
        state: WriteBatchState,
        extracted_count: int,
    ) -> None:
        """Plan unconsumed candidates against storage plus the batch overlay."""

        for index, candidate in enumerate(batch.candidates):
            if index in reconciliation.consumed_candidate_indexes:
                continue
            try:
                lookup_result = self.lookup.lookup(candidate.content, scope, candidate.type)
            except IndexLookupError as exc:
                raise _WriteAborted(
                    self._failure(
                        request_id,
                        ErrorCode.INDEX_UNAVAILABLE,
                        exc,
                        retryable=True,
                        extracted_count=extracted_count,
                        filtered_count=batch.filtered_count,
                    )
                ) from exc
            histories = state.visible_histories(
                [item.record for item in lookup_result.matches],
                candidate.type,
            )
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
                raise self._relation_abort(
                    request_id, exc, extracted_count, batch.filtered_count
                ) from exc
            except (
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            ) as exc:
                raise self._relation_abort(
                    request_id,
                    exc,
                    extracted_count,
                    batch.filtered_count,
                    retryable=True,
                ) from exc
            except AmbiguousDecision as exc:
                raise _WriteAborted(
                    self._failure(
                        request_id,
                        ErrorCode.AMBIGUOUS_RELATION,
                        exc,
                        extracted_count=extracted_count,
                        filtered_count=batch.filtered_count,
                    )
                ) from exc
            if plan.action in (Action.ADD, Action.UPDATE):
                plan = state.stage_materialized(
                    plan,
                    scope=scope,
                    memory_id=str(uuid4()),
                )
            elif plan.action is Action.DEFER:
                pending_resolution = self.deferred_collector.collect(
                    state.deferred_groups,
                    scope=scope,
                    plan=plan,
                    messages=messages,
                )
                plan = plan.model_copy(
                    update={"pending_resolution": pending_resolution}
                )
            state.record(plan, embedding=lookup_result.embedding)

    def _commit(
        self,
        *,
        request_id: str,
        input_hash: str,
        scope: MemoryScope,
        messages: list[Message],
        state: WriteBatchState,
        extracted_count: int,
        filtered_count: int,
    ) -> WriteReport:
        """Commit the ordered plan once, then let the coordinator sync the index."""

        try:
            return self.coordinator.commit(
                request_id=request_id,
                input_hash=input_hash,
                scope=scope,
                plans=state.plans,
                messages=messages,
                embeddings=state.embeddings,
                extracted_count=extracted_count,
                filtered_count=filtered_count,
            )
        except RequestAlreadyReserved as exc:
            current = self.repository.get_request(request_id, input_hash)
            if current is not None and current.report is not None:
                return current.report
            return self._failure(
                request_id,
                ErrorCode.REQUEST_IN_PROGRESS,
                exc,
                retryable=True,
            )
        except StaleMemoryState as exc:
            return self._failure(
                request_id,
                ErrorCode.REQUEST_IN_PROGRESS,
                exc,
                retryable=True,
                extracted_count=extracted_count,
                filtered_count=filtered_count,
            )
        except Exception as exc:
            current = self.repository.get_request(request_id, input_hash)
            if current is not None and current.report is not None:
                return current.report
            return self._failure(
                request_id,
                ErrorCode.STORAGE_FAILED,
                exc,
                extracted_count=extracted_count,
                filtered_count=filtered_count,
            )

    @staticmethod
    def _relation_abort(
        request_id: str,
        error: Exception,
        extracted_count: int,
        filtered_count: int,
        *,
        retryable: bool = False,
    ) -> _WriteAborted:
        return _WriteAborted(
            MemoryWritePipeline._failure(
                request_id=request_id,
                error_code=ErrorCode.RELATION_FAILED,
                error=error,
                retryable=retryable,
                extracted_count=extracted_count,
                filtered_count=filtered_count,
            )
        )

    @staticmethod
    def _failure(
        request_id: str,
        error_code: ErrorCode,
        error: Exception,
        *,
        retryable: bool = False,
        extracted_count: int = 0,
        filtered_count: int = 0,
    ) -> WriteReport:
        return WriteReport(
            request_id=request_id,
            status=WriteStatus.RETRYABLE if retryable else WriteStatus.FAILED,
            extracted_count=extracted_count,
            filtered_count=filtered_count,
            retryable=retryable,
            error_code=error_code,
            error_message=str(error),
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

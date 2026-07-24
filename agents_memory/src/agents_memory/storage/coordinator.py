from datetime import UTC, datetime
from uuid import uuid4

from agents_memory.embedding.base import Embedder
from agents_memory.embedding.openai import EmbeddingDimensionError
from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateResult,
    ErrorCode,
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    Message,
    RelationKind,
    SourceKind,
    Validity,
    WriteReport,
    WriteStatus,
)
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import IndexModelMismatch, MemoryIndex


class RequestAlreadyReserved(RuntimeError):
    pass


class StorageCoordinator:
    def __init__(
        self,
        repository: MemoryRepository,
        index: MemoryIndex,
        embedder: Embedder,
    ) -> None:
        self.repository = repository
        self.index = index
        self.embedder = embedder

    def commit(
        self,
        *,
        request_id: str,
        input_hash: str,
        scope: MemoryScope,
        plans: list[ActionPlan],
        messages: list[Message],
        embeddings: dict[int, list[float]],
        extracted_count: int,
        filtered_count: int,
    ) -> WriteReport:
        by_message = {message.message_id: message for message in messages}
        results: list[CandidateResult] = []
        initial_embeddings: dict[str, list[float]] = {}
        with self.repository.transaction() as connection:
            if not self.repository.reserve_request(
                request_id, input_hash, connection=connection
            ):
                raise RequestAlreadyReserved(request_id)
            for plan in plans:
                if plan.pending_resolution is not None:
                    by_message.update(
                        {
                            message.message_id: message
                            for message in plan.pending_resolution.source_messages
                        }
                    )
                if plan.action is Action.DEFER:
                    pending = plan.pending_resolution
                    if pending is None:
                        raise ValueError("DEFER plan requires pending resolution")
                    self.repository.save_pending_resolution(
                        pending, connection=connection
                    )
                    results.append(
                        CandidateResult(
                            candidate_index=plan.candidate_index,
                            content=plan.candidate.content,
                            action=Action.DEFER,
                            resolution_id=pending.id,
                            resolution_status=pending.status,
                            replaced_memory_ids=plan.target_ids,
                            matches=plan.matches,
                            missing_dimensions=pending.missing_dimensions,
                            reason=plan.reason or pending.reason,
                        )
                    )
                    continue
                if plan.action is Action.NOOP:
                    if plan.pending_resolution is not None:
                        self.repository.save_pending_resolution(
                            plan.pending_resolution, connection=connection
                        )
                    results.append(
                        CandidateResult(
                            candidate_index=plan.candidate_index,
                            content=plan.candidate.content,
                            action=Action.NOOP,
                            memory_id=plan.target_ids[0] if plan.target_ids else None,
                            resolution_id=(
                                plan.pending_resolution.id
                                if plan.pending_resolution
                                else None
                            ),
                            resolution_status=(
                                plan.pending_resolution.status
                                if plan.pending_resolution
                                else None
                            ),
                            replaced_memory_ids=plan.target_ids,
                            matches=plan.matches,
                            reason=plan.reason,
                        )
                    )
                    continue

                memory_id = plan.new_memory_id or str(uuid4())
                now = datetime.now(UTC)
                record = MemoryRecord(
                    id=memory_id,
                    scope=scope,
                    type=plan.candidate.type,
                    content=plan.candidate.content,
                    importance=plan.candidate.importance,
                    confidence=plan.candidate.confidence,
                    validity=Validity.ACTIVE,
                    created_at=now,
                    updated_at=now,
                    valid_from=now,
                    event_frame=plan.candidate.event_frame,
                    metadata=plan.candidate.metadata,
                )
                sources = tuple(
                    MemorySource(
                        memory_id=memory_id,
                        message_id=message_id,
                        role=by_message[message_id].role,
                        source_kind=SourceKind(
                            plan.candidate.metadata.get(
                                "_source_kinds", {}
                            ).get(
                                message_id,
                                plan.candidate.source_kind.value,
                            )
                        ),
                        excerpt=by_message[message_id].content,
                    )
                    for message_id in plan.candidate.source_message_ids
                )
                self.repository.save_memory(record, sources, connection=connection)

                if plan.action is Action.UPDATE:
                    validity = (
                        Validity.RETRACTED
                        if plan.relation is RelationKind.CORRECT
                        else Validity.SUPERSEDED
                    )
                    persistent_relation = (
                        RelationKind.CORRECTS
                        if plan.relation is RelationKind.CORRECT
                        else RelationKind.SUPERSEDES
                    )
                    for target_id in plan.target_ids:
                        self.repository.transition(
                            target_id,
                            validity,
                            MemoryRelation(
                                from_memory_id=target_id,
                                to_memory_id=memory_id,
                                relation=persistent_relation,
                            ),
                            connection=connection,
                        )
                        self.repository.enqueue_index_operation(
                            request_id,
                            target_id,
                            IndexOperationKind.DELETE,
                            connection=connection,
                        )

                self.repository.enqueue_index_operation(
                    request_id,
                    memory_id,
                    IndexOperationKind.UPSERT,
                    connection=connection,
                )
                initial_embeddings[memory_id] = embeddings[plan.candidate_index]
                if plan.pending_resolution is not None:
                    self.repository.save_pending_resolution(
                        plan.pending_resolution, connection=connection
                    )
                results.append(
                    CandidateResult(
                        candidate_index=plan.candidate_index,
                        content=plan.candidate.content,
                        action=plan.action,
                        memory_id=memory_id,
                        resolution_id=(
                            plan.pending_resolution.id
                            if plan.pending_resolution
                            else None
                        ),
                        resolution_status=(
                            plan.pending_resolution.status
                            if plan.pending_resolution
                            else None
                        ),
                        replaced_memory_ids=plan.target_ids,
                        matches=plan.matches,
                        reason=plan.reason,
                    )
                )

            committed_report = WriteReport(
                request_id=request_id,
                status=WriteStatus.RETRYABLE,
                results=tuple(results),
                extracted_count=extracted_count,
                filtered_count=filtered_count,
                sqlite_committed=True,
                index_synced=False,
                retryable=True,
                error_code=ErrorCode.INDEX_UNAVAILABLE,
            )
            self.repository.save_request(
                request_id,
                input_hash,
                "committed",
                committed_report,
                connection=connection,
            )

        return self._finish_sync(
            request_id,
            input_hash,
            committed_report,
            initial_embeddings,
        )

    def repair_request(self, request_id: str, input_hash: str) -> WriteReport:
        stored = self.repository.get_request(request_id, input_hash)
        if stored is None or stored.report is None:
            raise KeyError(request_id)
        return self._finish_sync(request_id, input_hash, stored.report, {})

    def repair_index(self) -> int:
        repaired = 0
        request_ids = {
            item.request_id
            for item in self.repository.list_index_operations()
            if item.status in (IndexOperationStatus.PENDING, IndexOperationStatus.FAILED)
        }
        for request_id in request_ids:
            stored = self.repository.get_request(request_id)
            if stored and stored.report:
                if not stored.report.retryable:
                    continue
                report = self._finish_sync(
                    request_id, stored.input_hash, stored.report, {}
                )
                if report.index_synced:
                    repaired += 1
            elif self._repair_operations_without_report(request_id):
                repaired += 1
        return repaired

    def rebuild_index(self) -> int:
        records = self.repository.list_all_active_memories()
        if not records:
            self.index.clear()
            return 0
        embeddings = self.embedder.embed([record.content for record in records])
        existing_ids = (
            set(self.index.list_ids()) if hasattr(self.index, "list_ids") else set()
        )
        for record, embedding in zip(records, embeddings, strict=True):
            self.index.upsert(record, embedding)
        active_ids = {record.id for record in records}
        for stale_id in existing_ids - active_ids:
            self.index.delete(stale_id)
        return len(records)

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        request_id = f"delete:{uuid4()}"
        with self.repository.transaction() as connection:
            deleted = self.repository.delete_memory(
                memory_id, user_id, connection=connection
            )
            if not deleted:
                return False
            self.repository.enqueue_index_operation(
                request_id,
                memory_id,
                IndexOperationKind.DELETE,
                connection=connection,
            )
        operation = next(
            item
            for item in self.repository.list_index_operations()
            if item.request_id == request_id
        )
        try:
            self.index.delete(memory_id)
            self.repository.mark_index_operation(
                operation.id, IndexOperationStatus.SYNCED  # type: ignore[arg-type]
            )
        except Exception as exc:
            self.repository.mark_index_operation(
                operation.id, IndexOperationStatus.FAILED, str(exc)  # type: ignore[arg-type]
            )
        return True

    def _finish_sync(
        self,
        request_id: str,
        input_hash: str,
        report: WriteReport,
        embeddings: dict[str, list[float]],
    ) -> WriteReport:
        operations = [
            item
            for item in self.repository.list_index_operations()
            if item.request_id == request_id
            and item.status in (IndexOperationStatus.PENDING, IndexOperationStatus.FAILED)
        ]
        all_synced = True
        terminal_error = False
        last_error: str | None = None
        for operation in operations:
            try:
                if operation.kind is IndexOperationKind.DELETE:
                    self.index.delete(operation.memory_id)
                else:
                    record = self.repository.get_memory(operation.memory_id)
                    if record is None:
                        self.index.delete(operation.memory_id)
                    else:
                        embedding = embeddings.get(operation.memory_id)
                        if embedding is None:
                            embedding = self.embedder.embed([record.content])[0]
                        self.index.upsert(record, embedding)
                self.repository.mark_index_operation(
                    operation.id, IndexOperationStatus.SYNCED  # type: ignore[arg-type]
                )
            except Exception as exc:
                all_synced = False
                terminal_error = terminal_error or isinstance(
                    exc, (EmbeddingDimensionError, IndexModelMismatch)
                )
                last_error = str(exc)
                self.repository.mark_index_operation(
                    operation.id, IndexOperationStatus.FAILED, last_error  # type: ignore[arg-type]
                )

        final_report = report.model_copy(
            update={
                "status": (
                    WriteStatus.SUCCESS
                    if all_synced
                    else WriteStatus.FAILED
                    if terminal_error
                    else WriteStatus.RETRYABLE
                ),
                "index_synced": all_synced,
                "retryable": not all_synced and not terminal_error,
                "error_code": (
                    None
                    if all_synced
                    else ErrorCode.STORAGE_FAILED
                    if terminal_error
                    else ErrorCode.INDEX_UNAVAILABLE
                ),
                "error_message": last_error,
            }
        )
        self.repository.save_request(
            request_id,
            input_hash,
            "complete" if all_synced else "committed",
            final_report,
        )
        return final_report

    def _repair_operations_without_report(self, request_id: str) -> bool:
        operations = [
            item
            for item in self.repository.list_index_operations()
            if item.request_id == request_id
            and item.status in (IndexOperationStatus.PENDING, IndexOperationStatus.FAILED)
        ]
        all_synced = True
        for operation in operations:
            try:
                if operation.kind is IndexOperationKind.DELETE:
                    self.index.delete(operation.memory_id)
                else:
                    record = self.repository.get_memory(operation.memory_id)
                    if record is None:
                        self.index.delete(operation.memory_id)
                    else:
                        embedding = self.embedder.embed([record.content])[0]
                        self.index.upsert(record, embedding)
                self.repository.mark_index_operation(
                    operation.id, IndexOperationStatus.SYNCED  # type: ignore[arg-type]
                )
            except Exception as exc:
                all_synced = False
                self.repository.mark_index_operation(
                    operation.id,
                    IndexOperationStatus.FAILED,
                    str(exc),  # type: ignore[arg-type]
                )
        return bool(operations) and all_synced

"""索引管线编排。笔记 §2.6 / §2.9 / §2.10。

串联：collector → router → normalizer → chunker → embedder → 三索引 → registry。
两阶段执行（先 new/update 建新，后 delete/move）；写入前清残留；动作级失败隔离。

实施说明（update 一致性）：本轮无查询侧，``superseded`` 中间态标记的消费者
（查询时 ``status=active`` 过滤）尚未接入，且 BM25 / 父块 KV 不便承载 status
过滤。故 update 采用「先建新 + 物理删旧」——无并发查询时无脏读风险，三索引
一致、无垃圾残留。子块 ``status`` 字段本轮仍写入（``active``），查询侧接入后
可平滑切到「标 superseded + 延迟物理删」语义。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from agents_rag.chunking.base import Chunker
from agents_rag.cleaning.normalizer import Normalizer
from agents_rag.ingestion.actions import execute
from agents_rag.ingestion.collector import diff, scan_directory
from agents_rag.ingestion.registry import DocumentRegistry
from agents_rag.indexing.bm25_index import BM25Index
from agents_rag.indexing.cache import EmbeddingCache
from agents_rag.indexing.embedder import Embedder
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.indexing.vectorstore import VectorStore
from agents_rag.models import Action, ActionKind, DocumentRecord
from agents_rag.parsing.router import ParserRouter

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    counts: dict[str, int] = field(default_factory=dict)
    failed: list[tuple[str, str]] = field(default_factory=list)
    indexed_chunks: int = 0


class IngestPipeline:
    def __init__(
        self,
        *,
        registry: DocumentRegistry,
        router: ParserRouter,
        normalizer: Normalizer,
        chunker: Chunker,
        embedder: Embedder,
        cache: EmbeddingCache | None,
        vector_store: VectorStore,
        bm25: BM25Index,
        parent_store: ParentStore,
        namespace: str = "local",
    ):
        self.registry = registry
        self.router = router
        self.normalizer = normalizer
        self.chunker = chunker
        self.embedder = embedder
        self.cache = cache
        self.vector_store = vector_store
        self.bm25 = bm25
        self.parent_store = parent_store
        self.namespace = namespace

    def run(self, directory: str | Path) -> IngestReport:
        scan_items = scan_directory(directory, namespace=self.namespace)
        records = self.registry.all_records()
        actions = diff(scan_items, records)
        result = execute(actions, self._handle, on_error=self._on_error)
        return IngestReport(
            counts=result.counts,
            failed=[(a.doc_id, str(e)) for a, e in result.failed],
            indexed_chunks=self.vector_store.count(),
        )

    def _on_error(self, action: Action, err: BaseException) -> None:
        log.error("动作失败 %s %s: %s", action.kind.value, action.doc_id, err)

    def _handle(self, action: Action) -> None:
        if action.kind in (ActionKind.NEW, ActionKind.UPDATE):
            self._apply_new_or_update(action)
        elif action.kind is ActionKind.DELETE:
            self._apply_delete(action)
        elif action.kind is ActionKind.MOVE:
            self._apply_move(action)

    def _apply_new_or_update(self, action: Action) -> None:
        doc_id = action.doc_id
        self._delete_chunks(doc_id)  # 写入前清残留（幂等，笔记 §2.9）

        document = self.router.parse(action.source_path)
        if document is None:
            raise RuntimeError(f"解析失败/低质量: {action.source_path}")
        document = self.normalizer.normalize(document.model_copy(update={"doc_id": doc_id}))

        parents, children = self.chunker.chunk(document)
        version = (action.old_record.version + 1) if action.old_record else 1

        if children:
            vectors = self.embedder.embed([c.text for c in children], cache=self.cache)
            self.vector_store.upsert(children, vectors)
            self.bm25.upsert(children)
            self.parent_store.put_many(parents)

        self.registry.upsert(
            self._record(
                action,
                tuple(c.id for c in children),
                tuple(p.id for p in parents),
                version,
            )
        )

        # update：先建新后删旧（物理删旧 doc_id，见模块 docstring 说明）
        if action.old_record is not None and action.old_record.doc_id != doc_id:
            self._delete_chunks(action.old_record.doc_id)
            self.registry.delete(action.old_record.doc_id)

    def _apply_delete(self, action: Action) -> None:
        self._delete_chunks(action.doc_id)
        self.registry.delete(action.doc_id)

    def _apply_move(self, action: Action) -> None:
        rec = self.registry.get(action.doc_id)
        if rec is not None:
            self.registry.upsert(rec.model_copy(update={"source_path": action.source_path}))

    def _delete_chunks(self, doc_id: str) -> None:
        self.vector_store.delete_by_doc(doc_id)
        self.bm25.remove_by_doc(doc_id)
        self.parent_store.delete_by_doc(doc_id)

    def _record(
        self,
        action: Action,
        child_ids: tuple[str, ...],
        parent_ids: tuple[str, ...],
        version: int,
    ) -> DocumentRecord:
        return DocumentRecord(
            doc_id=action.doc_id,
            content_fingerprint=action.fingerprint,
            source_path=action.source_path,
            source_namespace=action.namespace,
            doc_type=action.doc_type,
            chunk_ids=child_ids,
            parent_chunk_ids=parent_ids,
            version=version,
            content_size=action.content_size,
        )

"""索引管线编排。笔记 §2.6 / §2.9 / §2.10 + §12.1.1（图片处理）。

串联：collector → router → normalizer → **图片处理** → chunker → embedder → 三索引 → registry。
两阶段执行（先 new/update 建新，后 delete/move）；写入前清残留；动作级失败隔离。

图片处理阶段（解析后、分块前）：对 IMAGE block 存原图 + 生成描述（GLM-4.5V）+
填回 block，使描述走向量化 / 双索引；image_ref 关联原图。

实施说明（update 一致性）：本轮无查询侧，``superseded`` 中间态标记的消费者
（查询时 ``status=active`` 过滤）尚未接入，且 BM25 / 父块 KV 不便承载 status
过滤。故 update 采用「先建新 + 物理删旧」——无并发查询时无脏读风险，三索引
一致、无垃圾残留。
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
from agents_rag.indexing.image_store import ImageStore, detect_format, image_content_hash
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.indexing.vectorstore import VectorStore
from agents_rag.indexing.contextualizer import ContextCache, OpenAIContextualizer
from agents_rag.indexing.vision_describer import ImageDescriptionCache, OpenAIVisionDescriber
from agents_rag.models import (
    Action,
    ActionKind,
    Block,
    BlockType,
    Document,
    DocumentRecord,
    ImageRecord,
    Section,
)
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
        image_store: ImageStore | None = None,
        vision_describer: OpenAIVisionDescriber | None = None,
        description_cache: ImageDescriptionCache | None = None,
        contextualizer: OpenAIContextualizer | None = None,
        context_cache: ContextCache | None = None,
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
        self.image_store = image_store
        self.vision_describer = vision_describer
        self.description_cache = description_cache
        self.contextualizer = contextualizer
        self.context_cache = context_cache
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

        # 图片处理阶段（解析后、分块前）：存原图 + 生成描述 + 填 block
        if self.image_store is not None and self.vision_describer is not None:
            document = self._process_images(document)

        parents, children = self.chunker.chunk(document)
        version = (action.old_record.version + 1) if action.old_record else 1

        # CR 阶段（分块后、embed 前）：为每个 chunk 生成客观定位前缀
        if self.contextualizer is not None:
            children = self._contextualize_children(children, document)

        if children:
            vectors = self.embedder.embed([c.indexed_text for c in children], cache=self.cache)
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

    def _process_images(self, document: Document) -> Document:
        """递归 section 树，对 IMAGE block 存原图 + 生成描述 + 填回 block。"""
        new_sections = tuple(
            self._process_section(s, document.doc_id) for s in document.sections
        )
        return document.model_copy(update={"sections": new_sections})

    def _process_section(self, sec: Section, doc_id: str) -> Section:
        new_blocks = tuple(self._process_block(b, doc_id) for b in sec.blocks)
        new_children = tuple(self._process_section(c, doc_id) for c in sec.children)
        return sec.model_copy(update={"blocks": new_blocks, "children": new_children})

    def _process_block(self, b: Block, doc_id: str) -> Block:
        if b.type is not BlockType.IMAGE or not b.image_data:
            return b
        data = b.image_data
        content_hash = image_content_hash(data)
        fmt = detect_format(data)
        # 图片级增量：content_hash 已存在则复用既有描述
        existing = self.image_store.find_by_hash(content_hash)
        if existing is not None:
            image_ref = f"{existing.image_id}.{existing.format}"
            return b.model_copy(
                update={"text": existing.description, "image_ref": image_ref, "image_data": None}
            )
        image_ref = self.image_store.put(data, doc_id, fmt)
        description = self.vision_describer.describe(
            data, content_hash=content_hash, fmt=fmt,
            cache=self.description_cache, caption=b.caption,
        )
        self.image_store.upsert_record(
            ImageRecord(
                image_id=content_hash,
                doc_id=doc_id,
                source_path=str(self.image_store.path_of(doc_id, image_ref)),
                page=b.page,
                caption=b.caption,
                description=description,
                format=fmt,
                content_hash=content_hash,
            )
        )
        return b.model_copy(
            update={"text": description, "image_ref": image_ref, "image_data": None}
        )

    def _apply_delete(self, action: Action) -> None:
        self._delete_chunks(action.doc_id)
        self.registry.delete(action.doc_id)

    def _apply_move(self, action: Action) -> None:
        rec = self.registry.get(action.doc_id)
        if rec is not None:
            self.registry.upsert(rec.model_copy(update={"source_path": action.source_path}))

    def _contextualize_children(self, children: list, document) -> list:
        """CR 阶段：为每个 child chunk 生成 context 前缀（客观定位）。"""
        from agents_rag.ingestion.fingerprint import text_fingerprint

        new_children = []
        for c in children:
            sec = f" · 章节：{c.section_path}" if c.section_path else ""
            doc_context = f"文档：{document.source}{sec}"
            ctx = self.contextualizer.contextualize(
                c.text,
                doc_context,
                text_hash=text_fingerprint(c.text),
                cache=self.context_cache,
            )
            new_children.append(c.model_copy(update={"context": ctx}))
        return new_children

    def _delete_chunks(self, doc_id: str) -> None:
        self.vector_store.delete_by_doc(doc_id)
        self.bm25.remove_by_doc(doc_id)
        self.parent_store.delete_by_doc(doc_id)
        if self.image_store is not None:
            self.image_store.delete_by_doc(doc_id)

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

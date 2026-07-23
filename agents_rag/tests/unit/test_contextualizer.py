"""Contextual Retrieval 测试：indexed_text / ContextCache / 集成。"""

from __future__ import annotations

from agents_rag.indexing.contextualizer import ContextCache
from agents_rag.models import Block, BlockType, ChildChunk, DocType, Document, Section


def test_indexed_text_with_context():
    c = ChildChunk(id="c1", parent_id="p1", doc_id="d1", text="chunk内容", context="客观定位")
    assert c.indexed_text == "客观定位\n\nchunk内容"


def test_indexed_text_without_context():
    c = ChildChunk(id="c1", parent_id="p1", doc_id="d1", text="chunk内容")
    assert c.indexed_text == "chunk内容"  # context 空 → indexed_text = text


def test_context_cache_versioned(tmp_path):
    cache = ContextCache(tmp_path / "ctx.sqlite")
    cache.put("hash1", "glm-4-flash", "定位文本")
    assert cache.get("hash1", "glm-4-flash") == "定位文本"
    assert cache.get("hash1", "other-model") is None  # 换模型不命中
    cache.close()


class _FakeContextualizer:
    model = "fake"

    def contextualize(self, chunk_text, doc_context, *, text_hash, cache=None):
        return f"[CTX] {chunk_text[:20]}"


def test_contextualize_children_integration(tmp_path):
    from agents_rag.chunking.parent_child import StructuralChunker
    from agents_rag.cleaning.normalizer import Normalizer
    from agents_rag.ingestion.registry import DocumentRegistry
    from agents_rag.indexing.bm25_index import BM25Index
    from agents_rag.indexing.chroma_store import ChromaStore
    from agents_rag.indexing.parent_store import ParentStore
    from agents_rag.pipeline.ingest import IngestPipeline

    doc = Document(
        doc_id="d1", source="test.md", doc_type=DocType.MARKDOWN,
        sections=(Section(heading="H", level=1, blocks=(
            Block(type=BlockType.PARAGRAPH, text="这是一段足够长的正文内容用于测试" * 5),
        )),),
    )
    pipe = IngestPipeline(
        registry=DocumentRegistry(tmp_path / "r.sqlite"),
        router=None, normalizer=Normalizer(),
        chunker=StructuralChunker(parent_max_size=1000, chunk_size=100, overlap=10),
        embedder=None, cache=None,
        vector_store=ChromaStore(tmp_path / "c"),
        bm25=BM25Index(), parent_store=ParentStore(tmp_path / "p"),
        contextualizer=_FakeContextualizer(), context_cache=None,
    )
    children = pipe.chunker.chunk(doc)[1]
    contextualized = pipe._contextualize_children(children, doc)
    assert all(c.context.startswith("[CTX]") for c in contextualized)
    assert all(c.indexed_text != c.text for c in contextualized)

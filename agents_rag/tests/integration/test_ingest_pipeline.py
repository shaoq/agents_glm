"""ingest 管线集成测试（FakeEmbedder + 真实索引于 tmp_path）。"""

from __future__ import annotations

from agents_rag.chunking.parent_child import StructuralChunker
from agents_rag.cleaning.normalizer import Normalizer
from agents_rag.ingestion.registry import DocumentRegistry
from agents_rag.indexing.bm25_index import BM25Index
from agents_rag.indexing.chroma_store import ChromaStore
from agents_rag.indexing.embedder import Embedder
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.models import DocType
from agents_rag.parsing.markdown_parser import MarkdownParser
from agents_rag.parsing.router import ParserRouter
from agents_rag.pipeline.ingest import IngestPipeline

# 足够长以通过质量评估（chars/page ≥ 50）
_BODY = "这是用于检索增强生成测试的正文段落，包含足够多的中文字符以通过解析质量评估。" * 2


class FakeEmbedder(Embedder):
    def __init__(self) -> None:
        self.max_batch = 64
        self.max_concurrency = 1

    @property
    def model(self) -> str:
        return "fake"

    @property
    def dim(self) -> int:
        return 8

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[(float(i % 7)) / 7] * 8 for i, _ in enumerate(texts)]


def _make_pipeline(tmp_path) -> IngestPipeline:
    registry = DocumentRegistry(tmp_path / "reg.sqlite")
    router = ParserRouter({DocType.MARKDOWN: MarkdownParser(), DocType.TXT: MarkdownParser()})
    return IngestPipeline(
        registry=registry,
        router=router,
        normalizer=Normalizer(),
        chunker=StructuralChunker(parent_max_size=1000, chunk_size=100, overlap=10),
        embedder=FakeEmbedder(),
        cache=None,
        vector_store=ChromaStore(tmp_path / "chroma"),
        bm25=BM25Index(),
        parent_store=ParentStore(tmp_path / "parents"),
    )


def test_ingest_full_aligns_indexes(tmp_path):
    (tmp_path / "a.md").write_text(f"# 标题\n{_BODY}\n\n## 子标题\n{_BODY}\n", encoding="utf-8")
    pipe = _make_pipeline(tmp_path)
    report = pipe.run(tmp_path)

    assert report.counts.get("new", 0) == 1
    assert report.indexed_chunks > 0
    assert pipe.vector_store.count() == pipe.bm25.count()  # 双索引对齐
    recs = pipe.registry.list()
    assert len(recs) == 1
    assert len(recs[0].chunk_ids) == pipe.vector_store.count()  # 注册表一致


def test_ingest_second_run_skips(tmp_path):
    (tmp_path / "a.md").write_text(f"# 标题\n{_BODY}\n", encoding="utf-8")
    pipe = _make_pipeline(tmp_path)
    pipe.run(tmp_path)
    report2 = pipe.run(tmp_path)

    assert report2.counts.get("skipped", 0) == 1
    assert report2.counts.get("new", 0) in (None, 0)
    assert pipe.vector_store.count() == report2.indexed_chunks


def test_ingest_update_replaces_old(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(f"# 原标题\n{_BODY}\n", encoding="utf-8")
    pipe = _make_pipeline(tmp_path)
    pipe.run(tmp_path)
    old_doc_id = pipe.registry.list()[0].doc_id

    f.write_text(f"# 新标题\n{_BODY}额外不同的内容\n", encoding="utf-8")
    report2 = pipe.run(tmp_path)

    assert report2.counts.get("update", 0) == 1
    recs = pipe.registry.list()
    assert len(recs) == 1  # 旧 doc_id 已删
    assert recs[0].doc_id != old_doc_id
    assert pipe.vector_store.count() == pipe.bm25.count()


def test_ingest_delete_clears_all_indexes(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(f"# 标题\n{_BODY}\n", encoding="utf-8")
    pipe = _make_pipeline(tmp_path)
    pipe.run(tmp_path)

    f.unlink()
    report2 = pipe.run(tmp_path)

    assert report2.counts.get("delete", 0) == 1
    assert pipe.vector_store.count() == 0
    assert pipe.bm25.count() == 0
    assert pipe.registry.list() == []

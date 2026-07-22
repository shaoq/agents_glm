"""图片管线集成测试：_process_images + IMAGE block 描述入索引 + image_ref + ImageRecord。

用 FakeVisionDescriber 避免真实 GLM-4.5V 调用；直接测 _process_images 与分块/索引衔接。
"""

from __future__ import annotations

from agents_rag.chunking.parent_child import StructuralChunker
from agents_rag.cleaning.normalizer import Normalizer
from agents_rag.ingestion.registry import DocumentRegistry
from agents_rag.indexing.bm25_index import BM25Index
from agents_rag.indexing.chroma_store import ChromaStore
from agents_rag.indexing.image_store import ImageStore, image_content_hash
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.indexing.vision_describer import ImageDescriptionCache
from agents_rag.models import Block, BlockType, DocType, Document, Section
from agents_rag.pipeline.ingest import IngestPipeline


class _FakeVision:
    model = "fake"

    def describe(self, image_bytes, *, content_hash, fmt="png", cache=None, caption=None):
        return f"图片描述:{content_hash[:6]}"


def _make_pipeline(tmp_path) -> IngestPipeline:
    return IngestPipeline(
        registry=DocumentRegistry(tmp_path / "r.sqlite"),
        router=None,  # _process_images 不走 router
        normalizer=Normalizer(),
        chunker=StructuralChunker(parent_max_size=1000, chunk_size=100, overlap=10),
        embedder=None,
        cache=None,
        vector_store=ChromaStore(tmp_path / "c"),
        bm25=BM25Index(),
        parent_store=ParentStore(tmp_path / "p"),
        image_store=ImageStore(tmp_path / "img"),
        vision_describer=_FakeVision(),
        description_cache=ImageDescriptionCache(tmp_path / "desc.sqlite"),
    )


def test_process_images_fills_description_and_stores_original(tmp_path):
    img_data = b"\x89PNG\r\n\x1a\nfake image bytes"
    doc = Document(
        doc_id="d1",
        source="s",
        doc_type=DocType.PDF,
        sections=(
            Section(
                heading="H",
                level=1,
                blocks=(
                    Block(type=BlockType.IMAGE, text="", image_data=img_data, caption="图注", page=1),
                ),
            ),
        ),
    )
    pipe = _make_pipeline(tmp_path)

    out = pipe._process_images(doc)
    block = list(out.iter_blocks())[0]

    # 描述已填、image_ref 已设、image_data 已清
    assert block.text.startswith("图片描述:")
    assert block.image_ref is not None
    assert block.image_ref.endswith(".png")  # 含扩展名
    assert block.image_data is None
    # 原图已存 + ImageRecord 记录
    assert pipe.image_store.get("d1", block.image_ref) == img_data
    rec = pipe.image_store.get_record(image_content_hash(img_data))  # get_record 用 image_id(=hash)，非 image_ref
    assert rec.description.startswith("图片描述:") and rec.caption == "图注"
    # 注：描述缓存的查/写是 ZhipuVisionDescriber 内部行为（FakeVision 不模拟），
    # 已由 test_vision_describer.py 覆盖；此处只验 ImageRecord + 原图存储


def test_image_block_becomes_atomic_chunk_with_image_ref(tmp_path):
    img_data = b"img-bytes"
    doc = Document(
        doc_id="d1",
        source="s",
        doc_type=DocType.PDF,
        sections=(
            Section(
                heading="H",
                level=1,
                blocks=(
                    Block(type=BlockType.IMAGE, text="图片描述:柱状图", image_ref="img123", page=2),
                    Block(type=BlockType.PARAGRAPH, text="正文段落"),
                ),
            ),
        ),
    )
    parents, children = StructuralChunker(parent_max_size=1000, chunk_size=50, overlap=5).chunk(doc)
    # IMAGE 作为原子父块（独立 parent）
    img_parents = [p for p in parents if p.block_type is BlockType.IMAGE]
    assert img_parents and img_parents[0].image_ref == "img123"
    # 子块携带 image_ref
    img_children = [c for c in children if c.block_type is BlockType.IMAGE]
    assert img_children and img_children[0].image_ref == "img123"
    assert len(img_children) == 1  # 原子：单子块


def test_image_incremental_reuses_description(tmp_path):
    img_data = b"same-image"
    pipe = _make_pipeline(tmp_path)
    doc = Document(
        doc_id="d1",
        source="s",
        doc_type=DocType.PDF,
        sections=(
            Section(blocks=(Block(type=BlockType.IMAGE, text="", image_data=img_data),)),
        ),
    )
    pipe._process_images(doc)  # 首次：存原图 + 生成描述
    calls = {"n": 0}
    orig = pipe.vision_describer.describe

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    pipe.vision_describer.describe = counting  # type: ignore
    # 同图再次处理（content_hash 相同）→ 复用，不调 describe
    doc2 = Document(
        doc_id="d2",
        source="s",
        doc_type=DocType.PDF,
        sections=(
            Section(blocks=(Block(type=BlockType.IMAGE, text="", image_data=img_data),)),
        ),
    )
    out = pipe._process_images(doc2)
    assert calls["n"] == 0  # 图片级增量：未变不重算描述
    assert list(out.iter_blocks())[0].text.startswith("图片描述:")

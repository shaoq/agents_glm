"""chunking 测试：结构感知切父块、父子分块、元数据继承。"""

from __future__ import annotations

from agents_rag.chunking.parent_child import StructuralChunker, split_children
from agents_rag.chunking.structural import split_parents
from agents_rag.models import Block, BlockType, DocType, Document, ParentChunk, Section


def _doc(blocks: list[Block], doc_id: str = "d1") -> Document:
    sec = Section(heading=None, level=1, blocks=tuple(blocks))
    return Document(doc_id=doc_id, source="s", doc_type=DocType.MARKDOWN, sections=(sec,))


def test_split_parents_respects_block_boundary():
    b1 = Block(text="A" * 100)
    b2 = Block(text="B" * 100)
    parents = split_parents(_doc([b1, b2]), parent_max_size=120)
    assert len(parents) >= 2  # block 之间切
    # 不在 block 内部切断
    assert all(set(p.text.strip()) <= {"A", "B", "\n"} for p in parents)


def test_table_block_is_atomic_parent():
    tbl = Block(type=BlockType.TABLE, text="| a | b |\n|---|---|\n| 1 | 2 |")
    para = Block(text="段落" * 50)
    parents = split_parents(_doc([para, tbl]), parent_max_size=80)
    tbl_parents = [p for p in parents if p.block_type is BlockType.TABLE]
    assert tbl_parents
    assert "| a | b |" in tbl_parents[0].text


def test_child_id_encodes_parent():
    p = ParentChunk(id="d1__p0", doc_id="d1", text="x" * 200, section_path="A")
    children = split_children(p, chunk_size=50, overlap=10)
    assert children
    assert all(c.id.startswith("d1__p0__") for c in children)
    assert all(c.parent_id == "d1__p0" for c in children)


def test_table_parent_single_child():
    p = ParentChunk(
        id="d1__p0", doc_id="d1", text="x" * 200, block_type=BlockType.TABLE
    )
    children = split_children(p, chunk_size=50, overlap=10)
    assert len(children) == 1  # 原子父块不切


def test_chunker_metadata_complete():
    b = Block(text="正文内容" * 80, page=3)
    parents, children = StructuralChunker(
        parent_max_size=1000, chunk_size=50, overlap=10
    ).chunk(_doc([b]))
    assert parents and children
    c = children[0]
    assert c.doc_id == "d1"
    assert c.parent_id == parents[0].id
    assert c.version == 1
    assert c.status.value == "active"
    assert c.page == 3  # page 透传


def test_section_path_inherited():
    inner = Section(heading="内节", level=2, blocks=(Block(text="段落" * 50),))
    outer = Section(
        heading="外章", level=1, blocks=(Block(text="开头" * 50),), children=(inner,)
    )
    doc = Document(doc_id="d1", source="s", doc_type=DocType.MARKDOWN, sections=(outer,))
    parents = split_parents(doc, parent_max_size=1000)
    paths = {p.section_path for p in parents}
    assert any("外章" in p for p in paths)
    assert any("内节" in p for p in paths)

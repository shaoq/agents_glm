"""父子分块：父块切子块 + ``StructuralChunker`` 组合。笔记 §5.14。

- 子块由父块按 ``chunk_size`` / ``overlap`` 滑动窗口切出
- 子块 id 编码 ``parent_id``（``<parent_id>__<idx>``）
- 原子父块（表格 / 代码 / 列表）整体作单子块，不再切
- 子块继承 page / heading / section_path / block_type，并带 version=1 / status=active
"""

from __future__ import annotations

from agents_rag.chunking.base import Chunker
from agents_rag.chunking.structural import split_parents
from agents_rag.models import BlockType, ChildChunk, Document, ParentChunk


def split_children(
    parent: ParentChunk, *, chunk_size: int, overlap: int
) -> list[ChildChunk]:
    text = parent.text
    bt = parent.block_type
    if not text:
        return []
    # 原子父块或短文本：整体作单子块
    if bt is not BlockType.PARAGRAPH or len(text) <= chunk_size:
        return [_child(parent, text, 0, len(text), bt, 0)]

    step = max(1, chunk_size - overlap)
    children: list[ChildChunk] = []
    i, idx, n = 0, 0, len(text)
    while i < n:
        j = min(i + chunk_size, n)
        children.append(_child(parent, text[i:j], i, j, bt, idx))
        idx += 1
        if j >= n:
            break
        i += step
    return children


def _child(
    parent: ParentChunk, text: str, start: int, end: int, bt: BlockType, idx: int
) -> ChildChunk:
    return ChildChunk(
        id=f"{parent.id}__{idx}",
        parent_id=parent.id,
        doc_id=parent.doc_id,
        text=text,
        page=parent.page,
        heading=parent.heading,
        section_path=parent.section_path,
        block_type=bt,
        char_span=(start, end),
        image_ref=parent.image_ref,
    )


class StructuralChunker(Chunker):
    """结构感知切父块 + 父块切子块。"""

    def __init__(
        self, *, parent_max_size: int = 1800, chunk_size: int = 400, overlap: int = 64
    ):
        self.parent_max_size = parent_max_size
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, doc: Document) -> tuple[list[ParentChunk], list[ChildChunk]]:
        parents = split_parents(doc, parent_max_size=self.parent_max_size)
        children: list[ChildChunk] = []
        for p in parents:
            children.extend(
                split_children(p, chunk_size=self.chunk_size, overlap=self.overlap)
            )
        return parents, children

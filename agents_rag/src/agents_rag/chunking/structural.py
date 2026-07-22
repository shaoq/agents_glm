"""结构感知分块：切父块。笔记 §5.4 / §5.15。

- 尊重 block 边界：切断只发生在 block 之间，绝不切断段落内部
- 特殊结构豁免：表格 / 代码 / 列表作为原子父块
- 父块大小受 ``parent_max_size`` 约束（超则切，按 block 边界）
- 父块继承 section_path / heading / page
"""

from __future__ import annotations

from agents_rag.models import BlockType, Document, ParentChunk

_ATOMIC = {BlockType.TABLE, BlockType.CODE, BlockType.LIST}


def split_parents(doc: Document, *, parent_max_size: int) -> list[ParentChunk]:
    parents: list[ParentChunk] = []
    idx = 0
    # 缓冲: (text, page, heading, section_path)
    buf: list[tuple[str, int | None, str | None, str]] = []

    def buf_len() -> int:
        return sum(len(t) + 2 for t, *_ in buf)

    def emit(
        text: str,
        page: int | None,
        heading: str | None,
        path: str,
        bt: BlockType,
    ) -> None:
        nonlocal idx
        text = text.strip()
        if not text:
            return
        parents.append(
            ParentChunk(
                id=f"{doc.doc_id}__p{idx}",
                doc_id=doc.doc_id,
                text=text,
                page=page,
                heading=heading,
                section_path=path,
                block_type=bt,
            )
        )
        idx += 1

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        page = next((p for _, p, _, _ in buf if p is not None), None)
        emit(
            "\n\n".join(t for t, *_ in buf),
            page,
            buf[0][2],
            buf[0][3],
            BlockType.PARAGRAPH,
        )
        buf = []

    stack: list[str] = []

    def walk(sections) -> None:
        for sec in sections:
            flush()  # section 边界：结构感知切分（不跨 section 合并段落）
            if sec.heading:
                stack.append(sec.heading)
            path = " > ".join(stack)
            for b in sec.blocks:
                if not b.text:
                    continue
                if b.type in _ATOMIC:
                    flush()
                    emit(b.text, b.page, sec.heading, path, b.type)
                    continue
                if buf and buf_len() + len(b.text) > parent_max_size:
                    flush()
                buf.append((b.text, b.page, sec.heading, path))
            walk(sec.children)
            if sec.heading:
                stack.pop()

    walk(doc.sections)
    flush()
    return parents

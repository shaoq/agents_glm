"""Markdown 解析：标题层级 → 嵌套 sections。笔记 §3.3。

按 ``#`` 层级产出扁平 ``SectionNode``（同一标题下的段落 / 表格聚合进该节点 blocks），
再由 ``build_nested_sections`` 组织成嵌套 frozen 树。
"""

from __future__ import annotations

import re
from pathlib import Path

from agents_rag.models import Block, BlockType, Document, DocType, Section
from agents_rag.parsing.base import Parser
from agents_rag.parsing.tree import SectionNode, build_nested_sections

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# markdown 表格行（含分隔行）
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def md_to_sections(text: str) -> list[Section]:
    """按 ``#`` 标题层级产出嵌套 sections；表格聚合为 table block，段落聚合为 paragraph block。"""
    nodes: list[SectionNode] = []
    cur: SectionNode | None = None
    para_lines: list[str] = []
    table_lines: list[str] = []

    def ensure_cur() -> SectionNode:
        """标题前的内容归到一个无标题 section（level=1）。"""
        nonlocal cur
        if cur is None:
            cur = SectionNode(level=1, heading=None)
            nodes.append(cur)
        return cur

    def flush_para() -> None:
        nonlocal para_lines
        if para_lines:
            body = "\n".join(para_lines).strip()
            if body:
                ensure_cur().blocks.append(Block(type=BlockType.PARAGRAPH, text=body))
            para_lines = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            md = "\n".join(table_lines)
            ensure_cur().blocks.append(
                Block(type=BlockType.TABLE, text=md, table_data={"markdown": md})
            )
            table_lines = []

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush_para()
            flush_table()
            cur = SectionNode(level=len(m.group(1)), heading=m.group(2).strip())
            nodes.append(cur)
            continue
        if _TABLE_ROW.match(line):
            flush_para()
            table_lines.append(line)
        else:
            flush_table()
            para_lines.append(line)

    flush_para()
    flush_table()

    if not nodes:
        body = text.strip()
        nodes.append(
            SectionNode(
                level=1,
                heading=None,
                blocks=[Block(type=BlockType.PARAGRAPH, text=body)] if body else [],
            )
        )

    return build_nested_sections(nodes)


class MarkdownParser(Parser):
    supported_types = (DocType.MARKDOWN,)

    def parse(self, path: str | Path) -> Document:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        sections = tuple(md_to_sections(text))
        return Document(
            doc_id="",
            source=str(path),
            doc_type=DocType.MARKDOWN,
            sections=sections,
        )

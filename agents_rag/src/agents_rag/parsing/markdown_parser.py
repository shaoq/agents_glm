"""Markdown 解析：标题层级 → sections。天然结构化，最省心。笔记 §3.3。"""

from __future__ import annotations

import re
from pathlib import Path

from agents_rag.models import Block, BlockType, Document, DocType, Section
from agents_rag.parsing.base import Parser

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# markdown 表格行（含分隔行）
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def md_to_sections(text: str) -> list[Section]:
    """按 ``#`` 标题切分；表格行聚合成 table block，其余聚合成 paragraph block。"""
    sections: list[Section] = []
    cur_heading: str | None = None
    cur_level = 1
    para_lines: list[str] = []
    table_lines: list[str] = []

    def flush_table() -> None:
        if table_lines:
            md = "\n".join(table_lines)
            sections.append(
                Section(
                    heading=cur_heading,
                    level=cur_level,
                    blocks=(Block(type=BlockType.TABLE, text=md, table_data={"markdown": md}),),
                )
            )
            table_lines.clear()

    def flush_para() -> None:
        if para_lines:
            body = "\n".join(para_lines).strip()
            if body:
                sections.append(
                    Section(
                        heading=cur_heading,
                        level=cur_level,
                        blocks=(Block(type=BlockType.PARAGRAPH, text=body),),
                    )
                )
            para_lines.clear()

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush_para()
            flush_table()
            cur_level = len(m.group(1))
            cur_heading = m.group(2).strip()
            continue
        if _TABLE_ROW.match(line):
            flush_para()
            table_lines.append(line)
        else:
            flush_table()
            para_lines.append(line)

    flush_para()
    flush_table()

    if not sections:
        body = text.strip()
        sections.append(
            Section(
                heading=None,
                level=1,
                blocks=(Block(type=BlockType.PARAGRAPH, text=body),) if body else (),
            )
        )
    return sections


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

"""PDF 解析：docling（第三代文档理解模型）→ sections + 表格 block + page。

笔记 §3.2：PDF 是解析主战场，docling 表格 / 版面结构化强。
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents_rag.models import Block, BlockType, Document, DocType, Section
from agents_rag.parsing.base import Parser

log = logging.getLogger(__name__)

_HEADING_LABELS = {"title", "section_header", "header", "heading"}
_SKIP_LABELS = {"page_header", "page_footer", "footnote", "picture", "caption"}


class DoclingParser(Parser):
    supported_types = (DocType.PDF,)

    def __init__(self) -> None:
        from docling.document_converter import DocumentConverter

        self._conv = DocumentConverter()

    def parse(self, path: str | Path) -> Document:
        result = self._conv.convert(str(path))
        doc = result.document

        sections: list[Section] = []
        cur_heading: str | None = None
        cur_level = 1
        cur_blocks: list[Block] = []

        def flush() -> None:
            nonlocal cur_blocks
            if cur_heading is not None or cur_blocks:
                sections.append(
                    Section(heading=cur_heading, level=cur_level, blocks=tuple(cur_blocks))
                )
                cur_blocks = []

        for item, level in doc.iterate_items():
            label = str(getattr(item, "label", "")).lower()
            if label in _SKIP_LABELS:
                continue
            page = _page_of(item)
            if label in _HEADING_LABELS:
                flush()
                cur_heading = (getattr(item, "text", "") or "").strip() or None
                cur_level = max(1, level)
                continue
            if label == "table":
                md = _table_markdown(item, doc)
                if md:
                    cur_blocks.append(
                        Block(
                            type=BlockType.TABLE,
                            text=md,
                            page=page,
                            table_data={"markdown": md},
                        )
                    )
                continue
            txt = (getattr(item, "text", "") or "").strip()
            if txt:
                cur_blocks.append(Block(type=BlockType.PARAGRAPH, text=txt, page=page))

        flush()
        if not sections:
            sections.append(Section(heading=None, level=1, blocks=()))
        return Document(
            doc_id="",
            source=str(path),
            doc_type=DocType.PDF,
            sections=tuple(sections),
        )


def _page_of(item: object) -> int | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    p = prov[0]
    return getattr(p, "page", None) or getattr(p, "page_no", None)


def _table_markdown(item: object, doc: object) -> str:
    """docling TableItem 导出 markdown，兼容不同签名。"""
    fn = getattr(item, "export_to_markdown", None)
    if callable(fn):
        try:
            return fn(doc=doc).strip()  # type: ignore[call-arg]
        except TypeError:
            try:
                return fn().strip()  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001
                return ""
    return (getattr(item, "text", "") or "").strip()

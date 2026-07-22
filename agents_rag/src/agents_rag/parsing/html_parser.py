"""HTML 解析：trafilatura 抽正文 + bs4 兜底清洗 → sections。笔记 §3.3。"""

from __future__ import annotations

from pathlib import Path

import trafilatura

from agents_rag.models import Block, BlockType, Document, DocType, Section
from agents_rag.parsing.base import Parser
from agents_rag.parsing.markdown_parser import md_to_sections


class HTMLParser(Parser):
    supported_types = (DocType.HTML,)

    def parse(self, path: str | Path) -> Document:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
        # trafilatura 抽正文（去导航 / 广告 / 侧栏），输出 markdown 便于复用切分
        extracted = trafilatura.extract(raw, output_format="markdown") or ""
        text = extracted.strip()
        if text:
            sections = tuple(md_to_sections(text))
        else:
            sections = (
                Section(heading=None, level=1, blocks=(Block(type=BlockType.PARAGRAPH, text=""),)),
            )
        return Document(
            doc_id="",
            source=str(path),
            doc_type=DocType.HTML,
            sections=sections,
        )

"""ParserRouter：按扩展名路由 + 质量评估降级 + 全失败跳过。笔记 §3.6 / §3.9。

原则：永远有兜底，单文件失败 / 低质量不拖垮整批（跳过 + 日志）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from agents_rag.ingestion.collector import doc_type_for
from agents_rag.models import DocType, Document
from agents_rag.parsing.base import Parser
from agents_rag.parsing.quality import assess_quality

log = logging.getLogger(__name__)


def _as_chain(v: Parser | Iterable[Parser]) -> list[Parser]:
    return [v] if isinstance(v, Parser) else list(v)


class ParserRouter:
    """解析调度中心：按 doc_type 取降级链，逐级试，达标即返回。"""

    def __init__(self, parsers: dict[DocType, Parser | Iterable[Parser]]):
        self._parsers: dict[DocType, list[Parser]] = {
            k: _as_chain(v) for k, v in parsers.items()
        }

    @classmethod
    def with_defaults(cls, *, enable_pdf: bool = True) -> ParserRouter:
        """默认路由：md/html/txt/office + 可选 PDF(docling)。"""
        from agents_rag.parsing.html_parser import HTMLParser
        from agents_rag.parsing.markdown_parser import MarkdownParser
        from agents_rag.parsing.office_parser import DocxParser, PptxParser, XlsxParser

        parsers: dict[DocType, Parser | list[Parser]] = {
            DocType.MARKDOWN: MarkdownParser(),
            DocType.HTML: HTMLParser(),
            DocType.TXT: MarkdownParser(),  # 纯文本复用 md 切分
            DocType.DOCX: DocxParser(),
            DocType.XLSX: XlsxParser(),
            DocType.PPTX: PptxParser(),
        }
        if enable_pdf:
            from agents_rag.parsing.docling_parser import DoclingParser

            parsers[DocType.PDF] = [DoclingParser()]
        return cls(parsers)

    def parse(self, path: str | Path) -> Document | None:
        dt = doc_type_for(path)
        if dt is None or dt not in self._parsers:
            log.warning("无可用 parser，跳过: %s (type=%s)", path, dt)
            return None
        for parser in self._parsers[dt]:
            try:
                doc = parser.parse(path)
            except Exception as e:  # noqa: BLE001 — 单 parser 失败试下一个
                log.warning("parser %s 失败 %s: %s", type(parser).__name__, path, e)
                continue
            if doc is None:
                continue
            report = assess_quality(doc)
            if not report.is_poor():
                return doc
            log.info("低质量将降级 %s: chars/page=%.0f", path, report.chars_per_page)
        log.warning("全部 parser 失败/低质量，跳过: %s", path)
        return None

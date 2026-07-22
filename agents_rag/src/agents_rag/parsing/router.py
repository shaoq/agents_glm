"""ParserRouter：诊断式降级。笔记 §3.6 / §3.9。

主解析器结果经 ``poor_reason`` 诊断（scan / layout）→ 按原因选**对应**兜底
（scan → OCR、layout → MinerU），**只降一级**；兜底仍 poor 或无对应兜底则跳过。
原则：单文件失败 / 低质量不拖垮整批（跳过 + 日志）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents_rag.ingestion.collector import doc_type_for
from agents_rag.models import DocType, Document
from agents_rag.parsing.base import Parser
from agents_rag.parsing.quality import assess_quality

log = logging.getLogger(__name__)


class ParserRouter:
    """解析调度中心：primary + 按 poor_reason 的诊断式兜底。"""

    def __init__(
        self,
        primary: dict[DocType, Parser],
        fallbacks: dict[DocType, dict[str, Parser]] | None = None,
    ):
        self._primary = primary
        self._fallbacks = fallbacks or {}

    @classmethod
    def with_defaults(cls, *, enable_pdf: bool = True) -> ParserRouter:
        """默认路由：md/html/txt/office + 可选 PDF(docling + 诊断式兜底)。"""
        from agents_rag.parsing.html_parser import HTMLParser
        from agents_rag.parsing.markdown_parser import MarkdownParser
        from agents_rag.parsing.office_parser import DocxParser, PptxParser, XlsxParser

        primary: dict[DocType, Parser] = {
            DocType.MARKDOWN: MarkdownParser(),
            DocType.HTML: HTMLParser(),
            DocType.TXT: MarkdownParser(),  # 纯文本复用 md 切分
            DocType.DOCX: DocxParser(),
            DocType.XLSX: XlsxParser(),
            DocType.PPTX: PptxParser(),
        }
        fallbacks: dict[DocType, dict[str, Parser]] = {}

        if enable_pdf:
            from agents_rag.parsing.docling_parser import DoclingParser

            primary[DocType.PDF] = DoclingParser()
            fb: dict[str, Parser] = {}
            try:
                from agents_rag.parsing.ocr_parser import OCRParser

                fb["scan"] = OCRParser()  # 扫描件兜底（docling OCR 也失败时）
            except ImportError:
                pass
            try:
                from agents_rag.parsing.mineru_parser import MinerUParser

                fb["layout"] = MinerUParser()  # 复杂版面兜底（mineru 装了才配）
            except (ImportError, Exception):  # noqa: BLE001 — mineru 未装/初始化失败则不配
                pass
            fallbacks[DocType.PDF] = fb

        return cls(primary, fallbacks)

    def parse(self, path: str | Path) -> Document | None:
        dt = doc_type_for(path)
        if dt is None or dt not in self._primary:
            log.warning("无可用 parser，跳过: %s (type=%s)", path, dt)
            return None

        # 主解析器
        try:
            doc = self._primary[dt].parse(path)
        except Exception as e:  # noqa: BLE001
            log.warning("主 parser 失败 %s: %s", path, e)
            doc = None

        if doc is not None:
            reason = assess_quality(doc).poor_reason()
            if reason is None:
                return doc  # 达标
            # 诊断式降级：按原因选对应兜底，只降一级
            log.info("低质量(%s)将降级 %s", reason, path)
            fb = self._fallbacks.get(dt, {}).get(reason)
            if fb is not None:
                try:
                    doc2 = fb.parse(path)
                except Exception as e:  # noqa: BLE001
                    log.warning("兜底 parser 失败 %s: %s", path, e)
                    doc2 = None
                if doc2 is not None and assess_quality(doc2).poor_reason() is None:
                    return doc2  # 兜底达标
                log.info("兜底仍低质量 %s", path)

        log.warning("解析失败/低质量，跳过: %s", path)
        return None

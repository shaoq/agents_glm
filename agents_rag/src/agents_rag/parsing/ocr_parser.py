"""OCR 兜底解析器：RapidOCR 强制对 PDF 图像 OCR（扫描件兜底）。笔记 §3.2 / §3.6。

docling 内置 OCR 已处理普通扫描件；本 parser 仅在 docling OCR 也失败
（``chars_per_page`` 极低 → ``poor_reason=scan``）时触发。产出简化 ``Document``
（按页 paragraph block）。依赖 PyMuPDF(``fitz``) 渲染 + ``rapidocr`` 识别。
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents_rag.models import Block, BlockType, Document, DocType, Section
from agents_rag.parsing.base import Parser

log = logging.getLogger(__name__)


def _extract_texts(result: object) -> list[str]:
    """从 rapidocr 结果提取文本，兼容 3.x ``RapidOCROutput`` 与旧版 list。"""
    if result is None:
        return []
    # rapidocr 3.x：RapidOCROutput(.txts)
    txts = getattr(result, "txts", None)
    if txts:
        return [t for t in txts if t]
    # 旧版：list of [box, text, score]
    texts: list[str] = []
    for item in result:  # type: ignore[union-attr]
        if item and len(item) >= 2 and item[1]:
            texts.append(item[1])
    return texts


class OCRParser(Parser):
    supported_types = (DocType.PDF,)

    def __init__(self) -> None:
        # 惰性：不在构造时加载模型，避免 with_defaults 阶段开销
        pass

    def parse(self, path: str | Path) -> Document | None:
        import numpy as np
        import fitz  # PyMuPDF
        from rapidocr import RapidOCR

        ocr = RapidOCR()
        pdf = fitz.open(str(path))
        blocks: list[Block] = []
        try:
            for i, page in enumerate(pdf):
                pix = page.get_pixmap(dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                texts = _extract_texts(ocr(img))
                if texts:
                    text = "\n".join(texts).strip()
                    if text:
                        blocks.append(Block(type=BlockType.PARAGRAPH, text=text, page=i + 1))
        finally:
            pdf.close()

        if not blocks:
            return None
        return Document(
            doc_id="",
            source=str(path),
            doc_type=DocType.PDF,
            sections=(Section(heading=None, level=1, blocks=tuple(blocks)),),
        )

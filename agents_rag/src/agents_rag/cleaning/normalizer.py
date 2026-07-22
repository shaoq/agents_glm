"""清洗归一化：按 doc_type 轻量归一化。笔记 §4。

原则：克制度——只动文本层（空白 / 全半角 / 多空行），**不破坏结构**
（标题层级 / 表格 / 列表）、**保留位置元数据**（page / heading / bbox）。
"""

from __future__ import annotations

import re
import unicodedata

from agents_rag.models import Block, Document, Section

# 含全角空格
_MULTI_WS = re.compile(r"[ \t　]+")
_MULTI_NL = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """NFKC 全半角归一 + 压缩多余空白 / 空行。"""
    text = unicodedata.normalize("NFKC", text)
    text = _MULTI_WS.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


class Normalizer:
    """轻量清洗，保留结构与位置元数据。"""

    def normalize(self, doc: Document) -> Document:
        sections = tuple(self._section(s) for s in doc.sections)
        return doc.model_copy(update={"sections": sections})

    def _section(self, sec: Section) -> Section:
        blocks = tuple(self._block(b) for b in sec.blocks)
        children = tuple(self._section(c) for c in sec.children)
        return sec.model_copy(update={"blocks": blocks, "children": children})

    def _block(self, b: Block) -> Block:
        if not b.text:
            return b
        return b.model_copy(update={"text": clean_text(b.text)})

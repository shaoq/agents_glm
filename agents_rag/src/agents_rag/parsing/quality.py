"""解析质量评估。笔记 §3.7 / §3.9。

从 ``Document`` 聚合快信号（每页字符数、表格数、乱码占比、标题层级），
作为降级链判据与（未来）HITL 的输入。
"""

from __future__ import annotations

import re

from agents_rag.models import BlockType, Document, QualityReport

# 连续替换符 / 大段空白视为乱码信号
_GARBAGE = re.compile(r"[�]\s*[�]|[�]{2,}")


def assess_quality(doc: Document) -> QualityReport:
    blocks = list(doc.iter_blocks())
    text = "\n".join(b.text for b in blocks)
    chars = len(text)

    pages_with = [b.page for b in blocks if b.page is not None]
    page_max = max(pages_with, default=0)
    pages = max(page_max, 1)  # 无页码信息时按 1 页计，避免误判扫描件

    tables = sum(1 for b in blocks if b.type is BlockType.TABLE)
    garbage = sum(len(m) for m in _GARBAGE.findall(text))
    garbage_ratio = min(garbage / (chars + 1), 1.0)

    levels = [s.level for s in doc.sections]
    depth = max(levels, default=0)

    return QualityReport(
        chars_per_page=chars / pages,
        table_count=tables,
        heading_depth=depth,
        garbage_ratio=garbage_ratio,
    )

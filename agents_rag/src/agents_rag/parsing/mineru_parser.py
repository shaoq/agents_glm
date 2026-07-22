"""MinerU 兜底解析器：复杂版面（公式 / 双栏 / 复杂表格）兜底。笔记 §3.2 / §3.5。

MinerU 依赖重（带模型），``__init__`` 用 ``try import`` 检测可用性；未安装则
``with_defaults`` 不把 MinerUParser 配进链（``layout`` poor 无兜底 → 跳过）。
解析产出复用 markdown 切分（MinerU 导出 markdown → ``md_to_sections``）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents_rag.models import Document, DocType
from agents_rag.parsing.base import Parser
from agents_rag.parsing.markdown_parser import md_to_sections

log = logging.getLogger(__name__)


class MinerUParser(Parser):
    supported_types = (DocType.PDF,)

    def __init__(self) -> None:
        # 检测 MinerU 可用性；未装 raise ImportError → with_defaults 不配进链
        try:
            import magic_pdf  # noqa: F401  # MinerU (PDF-Extract-Kit) 旧包名
        except ImportError:
            try:
                import mineru  # noqa: F401  # 新包名
            except ImportError as e:
                raise ImportError(
                    "MinerU 未安装（pip install magic-pdf 或 mineru）"
                ) from e

    def parse(self, path: str | Path) -> Document | None:
        md = self._mineru_to_markdown(path)
        if not md or not md.strip():
            return None
        sections = tuple(md_to_sections(md))
        return Document(
            doc_id="", source=str(path), doc_type=DocType.PDF, sections=sections
        )

    def _mineru_to_markdown(self, path: str | Path) -> str:
        """调 MinerU 解析 PDF → markdown。MinerU API 跨版本差异大，用兼容 try。

        MinerU 需模型配置，具体 pipeline 按实际安装版本适配；失败返回空
        （router 视为兜底失败 → 跳过该文件）。
        """
        try:
            # 占位：MinerU 具体 pipeline（UNIPipe / CLI）按实际版本接入。
            # 装了 MinerU 后在此调其 API 导出 markdown，例如：
            #   from magic_pdf.pipe.UNIPipe import UNIPipe
            #   ...（模型配置 + pipeline + markdown 导出）
            log.warning("MinerU 解析尚未接入实际 API（占位实现）")
            return ""
        except Exception as e:  # noqa: BLE001
            log.warning("MinerU 解析失败: %s", e)
            return ""

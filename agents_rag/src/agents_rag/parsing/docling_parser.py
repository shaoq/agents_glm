"""PDF 解析：docling → 嵌套 sections + 表格/图片 block + page。

笔记 §3.2（PDF 主战场）+ §12.1.1（图片处理）。
图片：识别 `picture` → 提取原图字节（`ImageRef.pil_image`）+ caption → 产出 `IMAGE` block
（`text` 描述由 pipeline 阶段用 GLM-4.5V 生成并填充）。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from agents_rag.models import Block, BlockType, Document, DocType
from agents_rag.parsing.base import Parser
from agents_rag.parsing.tree import SectionNode, build_nested_sections

log = logging.getLogger(__name__)

_HEADING_LABELS = {"title", "section_header", "header", "heading"}
# picture 单独处理（产出 IMAGE block）；caption 独立 item 跳过（图片题注从 picture.captions 取）
_SKIP_LABELS = {"page_header", "page_footer", "footnote", "caption"}


class DoclingParser(Parser):
    supported_types = (DocType.PDF,)

    def __init__(self) -> None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # 开启图片提取（默认 False，否则 PictureItem.image 为 None）
        opts = PdfPipelineOptions(generate_picture_images=True)
        self._conv = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    def parse(self, path: str | Path) -> Document:
        result = self._conv.convert(str(path))
        doc = result.document

        nodes: list[SectionNode] = []
        cur: SectionNode | None = None

        for item, level in doc.iterate_items():
            label = str(getattr(item, "label", "")).lower()
            if label in _SKIP_LABELS:
                continue
            page = _page_of(item)
            if label in _HEADING_LABELS:
                cur = SectionNode(
                    level=max(1, level),
                    heading=(getattr(item, "text", "") or "").strip() or None,
                )
                nodes.append(cur)
                continue
            if cur is None:
                cur = SectionNode(level=1, heading=None)
                nodes.append(cur)
            if label == "table":
                md = _table_markdown(item, doc)
                if md:
                    cur.blocks.append(
                        Block(type=BlockType.TABLE, text=md, page=page, table_data={"markdown": md})
                    )
                continue
            if label == "picture":
                img_bytes, caption = _extract_image(item)
                if img_bytes is None:
                    continue  # 提取失败跳过（不中断批量）
                cur.blocks.append(
                    Block(
                        type=BlockType.IMAGE,
                        text="",  # 描述由 pipeline 阶段填充
                        page=page,
                        image_ref=None,
                        caption=caption,
                        image_data=img_bytes,
                    )
                )
                continue
            txt = (getattr(item, "text", "") or "").strip()
            if txt:
                cur.blocks.append(Block(type=BlockType.PARAGRAPH, text=txt, page=page))

        if not nodes:
            nodes.append(SectionNode(level=1, heading=None))

        sections = build_nested_sections(nodes)
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


def _extract_image(item: object) -> tuple[bytes | None, str | None]:
    """从 PictureItem 提取原图字节(PNG) + caption。失败返回 (None, caption)。"""
    captions = getattr(item, "captions", None) or []
    cap_texts = [t for t in (getattr(c, "text", "") or "" for c in captions) if t]
    caption = " ".join(cap_texts).strip() or None

    img_ref = getattr(item, "image", None)
    pil = getattr(img_ref, "pil_image", None) if img_ref is not None else None
    if pil is None:
        return None, caption
    try:
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue(), caption
    except Exception as e:  # noqa: BLE001
        log.warning("图片转字节失败: %s", e)
        return None, caption

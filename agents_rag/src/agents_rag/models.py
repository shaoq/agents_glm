"""核心数据结构（pydantic frozen，不可变）。

整合整体 spec §7 与知识构建笔记各小节：
- 解析输出 Document/Section/Block（笔记 §3.4）
- 分块产物 ParentChunk/ChildChunk（笔记 §5.14）
- 解析质量评估 QualityReport（笔记 §3.9）
- 文档注册表记录 DocumentRecord（笔记 §2.3）
- 五态动作 Action/ScanItem（笔记 §2.6）
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """固定入口，便于测试 monkeypatch。"""
    return datetime.now(timezone.utc)


# —— 枚举（str Enum 便于序列化与向量库 metadata 存储）——
class BlockType(str, Enum):
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    CODE = "code"
    IMAGE = "image"


class DocType(str, Enum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    HTML = "html"
    TXT = "txt"


class ChunkStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class DocStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class ActionKind(str, Enum):
    NEW = "new"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"
    SKIP = "skip"


# —— 解析输出（笔记 §3.4 统一文档模型）——
class Block(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: BlockType = BlockType.PARAGRAPH
    text: str = ""
    page: int | None = None
    bbox: tuple[float, ...] | None = None
    # type=table 时：{"rows": [[...], ...]} 或 {"markdown": "..."}
    table_data: dict[str, Any] | None = None
    # type=image 时：image_ref 关联原图；caption 为图注；image_data 为解析层临时字段
    # （pipeline 存盘 + 生成描述后清空，不进向量库 metadata）
    image_ref: str | None = None
    caption: str | None = None
    image_data: bytes | None = None


class Section(BaseModel):
    model_config = ConfigDict(frozen=True)

    heading: str | None = None
    level: int = 1
    blocks: tuple[Block, ...] = ()
    children: tuple[Section, ...] = ()


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    source: str
    doc_type: DocType
    sections: tuple[Section, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    def iter_blocks(self):
        """深度优先遍历所有 block（含嵌套 section）。"""
        def _walk(sections):
            for sec in sections:
                yield sec
                yield from _walk(sec.children)
        for sec in _walk(self.sections):
            yield from sec.blocks


# —— 分块产物（笔记 §5.14）——
class ParentChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    doc_id: str
    text: str
    page: int | None = None
    heading: str | None = None
    section_path: str = ""
    block_type: BlockType = BlockType.PARAGRAPH  # 表格/代码作原子父块（豁免切断）
    image_ref: str | None = None  # type=image 时关联原图


class ChildChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    parent_id: str
    doc_id: str
    text: str
    page: int | None = None
    heading: str | None = None
    section_path: str = ""
    block_type: BlockType = BlockType.PARAGRAPH
    char_span: tuple[int, int] = (0, 0)
    version: int = 1
    status: ChunkStatus = ChunkStatus.ACTIVE
    image_ref: str | None = None  # type=image 时关联原图（chunk 级，供查询侧回传）

    def metadata_dict(self) -> dict[str, Any]:
        """供向量库 metadata 存储（Chroma metadata 值须为基础类型）。"""
        return {
            "doc_id": self.doc_id,
            "parent_id": self.parent_id,
            "page": self.page if self.page is not None else -1,
            "heading": self.heading or "",
            "section_path": self.section_path,
            "block_type": self.block_type.value,
            "version": self.version,
            "status": self.status.value,
            "char_start": self.char_span[0],
            "char_end": self.char_span[1],
            "image_ref": self.image_ref or "",
        }


# —— 解析质量评估（笔记 §3.9）——
class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    chars_per_page: float = 0.0
    table_count: int = 0
    heading_depth: int = 0
    garbage_ratio: float = 0.0

    def is_poor(
        self,
        *,
        min_chars_per_page: float = 50.0,
        max_garbage_ratio: float = 0.3,
    ) -> bool:
        """低质量判据：每页字符过少（疑似扫描件）或乱码占比过高。"""
        if self.chars_per_page < min_chars_per_page:
            return True
        if self.garbage_ratio > max_garbage_ratio:
            return True
        return False


# —— 文档注册表记录（笔记 §2.3，sqlite 持久化的真相源）——
class DocumentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    content_fingerprint: str
    source_path: str
    source_namespace: str = "local"
    doc_type: DocType = DocType.TXT
    chunk_ids: tuple[str, ...] = ()
    parent_chunk_ids: tuple[str, ...] = ()
    version: int = 1
    content_size: int = 0
    indexed_at: datetime = Field(default_factory=_utcnow)
    status: DocStatus = DocStatus.ACTIVE


# —— 五态检测（笔记 §2.6）——
class ScanItem(BaseModel):
    """扫描目录得到的一个文件项（含指纹）。"""

    model_config = ConfigDict(frozen=True)

    source_path: str
    source_namespace: str = "local"
    doc_type: DocType
    content_fingerprint: str
    content_size: int
    mtime: float


class Action(BaseModel):
    """五态动作：检测阶段产出，执行阶段消费。"""

    model_config = ConfigDict(frozen=True)

    kind: ActionKind
    doc_id: str
    source_path: str
    fingerprint: str
    doc_type: DocType
    namespace: str = "local"
    content_size: int = 0
    old_record: DocumentRecord | None = None


# —— 图片注册表记录（笔记 §12.1.1，原图存储 + 描述缓存增量）——
class ImageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    image_id: str  # = content_hash（图片身份）
    doc_id: str
    source_path: str  # 原图存储路径（含扩展名）
    page: int | None = None
    caption: str | None = None
    description: str = ""
    format: str = "png"  # 图片格式（mime: image/{format}）
    content_hash: str
    created_at: datetime = Field(default_factory=_utcnow)

"""Parser 抽象基类。笔记 §3.4 统一文档模型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from agents_rag.models import DocType, Document


class Parser(ABC):
    """文档解析器：将文件解析为统一 ``Document``。

    实现负责产出 sections / 类型化 blocks / 位置元数据；``doc_id`` 由编排层
    （用内容指纹）回填，此处留空串。
    """

    supported_types: tuple[DocType, ...] = ()

    @abstractmethod
    def parse(self, path: str | Path) -> Document:
        """解析文件，返回统一 Document。"""
        raise NotImplementedError

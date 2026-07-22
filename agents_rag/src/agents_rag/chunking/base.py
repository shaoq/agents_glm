"""Chunker 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agents_rag.models import ChildChunk, Document, ParentChunk


class Chunker(ABC):
    """分块器：``Document`` → (父块列表, 子块列表)。"""

    @abstractmethod
    def chunk(self, doc: Document) -> tuple[list[ParentChunk], list[ChildChunk]]:
        raise NotImplementedError

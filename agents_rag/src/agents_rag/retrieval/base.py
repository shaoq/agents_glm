"""检索抽象基类。笔记 §3。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agents_rag.models import RetrievalResult


class Retriever(ABC):
    """检索器：query → list[RetrievalResult]。"""

    @abstractmethod
    def retrieve(self, query: str, k: int = 20) -> list[RetrievalResult]:
        raise NotImplementedError

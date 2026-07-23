"""引用溯源：从 RetrievalResult 构造 Citation。笔记 §7.1。"""

from __future__ import annotations

from agents_rag.models import Citation, RetrievalResult


def make_citation(r: RetrievalResult, snippet_len: int = 100) -> Citation:
    """从检索结果构造引用（文档名 + 页码 + 原文片段）。"""
    snippet = r.text[:snippet_len] + "..." if len(r.text) > snippet_len else r.text
    return Citation(
        doc_id=r.doc_id,
        source_name=r.source_name or r.doc_id,
        page=r.page,
        snippet=snippet,
    )

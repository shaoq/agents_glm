"""上下文构建。笔记 §5。

AutoMerging → hash 去重 → token 预算截断 → 引用编号注入。
"""

from __future__ import annotations

import logging

from agents_rag.ingestion.fingerprint import text_fingerprint
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.models import RetrievalResult

log = logging.getLogger(__name__)


def _count_tokens(text: str) -> int:
    """tiktoken + 15% buffer（补偿 GLM tokenizer 偏差）。fallback 粗估。"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return int(len(enc.encode(text)) * 1.15)
    except Exception:  # noqa: BLE001
        return int(len(text) * 1.5)


class ContextBuilder:
    """构建给 LLM 的上下文：AutoMerging → 去重 → 预算 → 编号。"""

    def __init__(
        self,
        parent_store: ParentStore | None = None,
        merge_threshold: int = 2,
        max_tokens: int = 6000,
    ):
        self._parent_store = parent_store
        self._merge_threshold = merge_threshold
        self._max_tokens = max_tokens

    def auto_merge(self, results: list[RetrievalResult]) -> list[RetrievalResult]:
        """AutoMerging：同 parent_id ≥ threshold → 回传父块。"""
        if self._parent_store is None:
            return results
        groups: dict[str, list[RetrievalResult]] = {}
        for r in results:
            groups.setdefault(r.parent_id, []).append(r)
        merged: list[RetrievalResult] = []
        for pid, hits in groups.items():
            if len(hits) >= self._merge_threshold:
                parent = self._parent_store.get(hits[0].doc_id, pid)
                if parent:
                    merged.append(
                        RetrievalResult(
                            chunk_id=parent.id,
                            text=parent.text,
                            score=hits[0].score,
                            retriever="merged",
                            doc_id=parent.doc_id,
                            parent_id=parent.id,
                            page=parent.page,
                            heading=parent.heading,
                            section_path=parent.section_path,
                        )
                    )
                    continue
            merged.extend(hits)
        return merged

    def build(
        self, results: list[RetrievalResult], query: str = ""
    ) -> tuple[str, dict[int, RetrievalResult]]:
        """构建上下文 + 编号映射。返回 (context_str, id_map)。"""
        merged = self.auto_merge(results)
        # hash 去重
        seen: set[str] = set()
        deduped: list[RetrievalResult] = []
        for r in merged:
            fp = text_fingerprint(r.text)
            if fp not in seen:
                seen.add(fp)
                deduped.append(r)
        # token 预算截断（高分优先）
        budget = self._max_tokens
        selected: list[RetrievalResult] = []
        for r in sorted(deduped, key=lambda x: x.score, reverse=True):
            t = _count_tokens(r.text)
            if budget - t < 0:
                continue
            budget -= t
            selected.append(r)
        # 编号注入
        id_map: dict[int, RetrievalResult] = {}
        lines: list[str] = []
        for i, r in enumerate(selected, 1):
            id_map[i] = r
            source = r.source_name or r.doc_id
            page_str = f"第{r.page}页" if r.page else "页码未知"
            lines.append(f"[{i}]（{source}, {page_str}）{r.text}")
        return "\n\n".join(lines), id_map

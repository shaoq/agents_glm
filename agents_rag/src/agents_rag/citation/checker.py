"""引用校验：CitationChecker。笔记 §7.3。

正则提取 [N] → 与上下文编号集合比对 → 无效剔除 + Citation 构造。
"""

from __future__ import annotations

import re

from agents_rag.citation.sources import make_citation
from agents_rag.generation.prompts import CITATION_PATTERN
from agents_rag.models import Answer, AnswerStatus, RetrievalResult


class CitationChecker:
    def check(
        self,
        answer_text: str,
        id_map: dict[int, RetrievalResult],
    ) -> Answer:
        """校验回答引用编号，构造 Answer。"""
        cited_nums = set(int(m) for m in re.findall(CITATION_PATTERN, answer_text))
        valid_nums = cited_nums & set(id_map.keys())
        invalid_nums = cited_nums - set(id_map.keys())

        # 剔除无效引用
        clean_text = answer_text
        for n in invalid_nums:
            clean_text = clean_text.replace(f"[{n}]", "[无效引用]")

        # 构造有效引用
        citations = tuple(make_citation(id_map[n]) for n in sorted(valid_nums))
        used_ids = tuple(id_map[n].chunk_id for n in sorted(valid_nums))

        return Answer(
            text=clean_text,
            citations=citations,
            used_context_ids=used_ids,
            status=AnswerStatus.ANSWERED,
        )

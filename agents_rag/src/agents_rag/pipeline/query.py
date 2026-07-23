"""查询管线编排。笔记 §3.5/§4.5/§5/§6/§7。

串联：embed→双路召回→RRF→Rerank→AutoMerging→ContextBuilder→Generator→CitationChecker。
检索空兜底（不生成，省 LLM）。
"""

from __future__ import annotations

import logging

from agents_rag.citation.checker import CitationChecker
from agents_rag.citation.faithfulness import FaithfulnessChecker
from agents_rag.generation.context_builder import ContextBuilder
from agents_rag.generation.llm import Generator
from agents_rag.models import Answer, AnswerStatus
from agents_rag.retrieval.hybrid import HybridRetriever
from agents_rag.retrieval.reranker import Reranker

log = logging.getLogger(__name__)


class QueryPipeline:
    def __init__(
        self,
        *,
        hybrid_retriever: HybridRetriever,
        reranker: Reranker,
        context_builder: ContextBuilder,
        generator: Generator,
        citation_checker: CitationChecker,
        faithfulness_checker: FaithfulnessChecker | None = None,
        vector_top_k: int = 20,
        bm25_top_k: int = 20,
        rerank_top_n: int = 6,
    ):
        self._retriever = hybrid_retriever
        self._reranker = reranker
        self._context_builder = context_builder
        self._generator = generator
        self._citation_checker = citation_checker
        self._faithfulness_checker = faithfulness_checker
        self._vector_top_k = vector_top_k
        self._bm25_top_k = bm25_top_k
        self._rerank_top_n = rerank_top_n

    def ask(self, query: str) -> Answer:
        """查询管线：问题 → 带引用的可信回答。"""
        k = max(self._vector_top_k, self._bm25_top_k)

        # ①②③ 双路召回 + RRF 融合
        fused = self._retriever.retrieve(query, k=k)
        if not fused:
            return Answer(
                text="",
                status=AnswerStatus.NO_RESULT,
                message="未找到与您问题相关的内容。请确认相关文档已入库，或尝试换一种提问方式。",
            )

        # ④ Rerank 精排
        reranked = self._reranker.rerank(query, fused, top_n=self._rerank_top_n)
        if not reranked:
            return Answer(
                text="",
                status=AnswerStatus.NO_RESULT,
                message="未找到高度相关的内容。",
            )

        # ⑤⑥ AutoMerging + 上下文构建
        context_str, id_map = self._context_builder.build(reranked, query)
        if not context_str:
            return Answer(
                text="",
                status=AnswerStatus.NO_RESULT,
                message="未找到高度相关的内容。",
            )

        # ⑦ 生成
        answer_text = self._generator.generate(query, context_str)

        # ⑧ 引用校验
        answer = self._citation_checker.check(answer_text, id_map)

        # ⑨ Faithfulness 二次校验（可选，只打分不拦截）
        if self._faithfulness_checker is not None:
            score = self._faithfulness_checker.check(answer.text, context_str)
            answer = answer.model_copy(update={"faithfulness_score": score})

        return answer

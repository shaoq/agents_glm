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
from agents_rag.models import Answer, AnswerStatus, RetrievalResult
from agents_rag.retrieval.hybrid import HybridRetriever, rrf_fuse
from agents_rag.retrieval.query_rewriter import QueryRewriter
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
        rewriter: QueryRewriter | None = None,
        confidence_enabled: bool = False,
        confidence_threshold: float = 0.5,
        confidence_weight_rerank: float = 0.3,
        confidence_weight_citation: float = 0.3,
        confidence_weight_faithfulness: float = 0.4,
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
        self._rewriter = rewriter
        self._confidence_enabled = confidence_enabled
        self._confidence_threshold = confidence_threshold
        self._w_rerank = confidence_weight_rerank
        self._w_citation = confidence_weight_citation
        self._w_faith = confidence_weight_faithfulness
        self._vector_top_k = vector_top_k
        self._bm25_top_k = bm25_top_k
        self._rerank_top_n = rerank_top_n

    def ask(self, query: str) -> Answer:
        """查询管线：问题 → 带引用的可信回答。"""
        k = max(self._vector_top_k, self._bm25_top_k)

        # ①' 查询改写（可选）→ ①②③ 双路召回 + RRF 融合（双 query 融合）
        fused = self._retrieve_fused(query, k)
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

        # ⑩ 置信度聚合（可选，只标注不丢弃）
        if self._confidence_enabled:
            answer = self._aggregate_confidence(answer, id_map)

        return answer

    def _retrieve_fused(self, query: str, k: int) -> list[RetrievalResult]:
        """查询改写（可选）+ 双 query RRF 融合；失败/无改写回退原 query 单路。"""
        original = self._retriever.retrieve(query, k=k)
        if self._rewriter is None:
            return original
        rewritten = self._rewriter.rewrite(query)
        if not rewritten or rewritten.strip() == query.strip():
            return original  # 改写失败(None) / 已规范 → 走原路
        rewritten_fused = self._retriever.retrieve(rewritten, k=k)
        if not rewritten_fused:
            return original  # 改写路空 → 用原路
        return rrf_fuse(original, rewritten_fused)[:k]

    def _aggregate_confidence(self, answer: Answer, id_map: dict) -> Answer:
        """三信号加权聚合 → confidence → answered/low_confidence。"""
        # A. rerank min-max 归一化
        scores = [r.score for r in id_map.values()]
        if scores:
            mn, mx = min(scores), max(scores)
            rerank_norm = sum(
                (s - mn) / (mx - mn) if mx > mn else 1.0 for s in scores
            ) / len(scores)
        else:
            rerank_norm = 0.0
        # B. citation 通过率
        citation_rate = len(answer.used_context_ids) / len(id_map) if id_map else 0.0
        # C. faithfulness（可能 None）
        faith = answer.faithfulness_score

        wr, wc, wf = self._w_rerank, self._w_citation, self._w_faith
        if faith is not None:
            confidence = (wr * rerank_norm + wc * citation_rate + wf * faith) / (wr + wc + wf)
        elif (wr + wc) > 0:
            confidence = (wr * rerank_norm + wc * citation_rate) / (wr + wc)
        else:
            confidence = 0.0

        status = AnswerStatus.ANSWERED if confidence >= self._confidence_threshold else AnswerStatus.LOW_CONFIDENCE
        return answer.model_copy(update={"confidence": confidence, "status": status})

"""Faithfulness 校验测试：JSON 解析 + 集成（开/关）。"""

from __future__ import annotations

from agents_rag.citation.faithfulness import FaithfulnessChecker


def test_parse_score_half():
    raw = '[{"sentence": "a", "supported": true}, {"sentence": "b", "supported": false}]'
    assert FaithfulnessChecker._parse_score(raw) == 0.5


def test_parse_score_all_supported():
    raw = '[{"sentence": "a", "supported": true}]'
    assert FaithfulnessChecker._parse_score(raw) == 1.0


def test_parse_score_invalid():
    assert FaithfulnessChecker._parse_score("not json") is None


def test_parse_score_empty():
    assert FaithfulnessChecker._parse_score("[]") is None


def test_parse_score_json_embedded_in_text():
    raw = '结果：\n[{"sentence": "a", "supported": true}]\n完成'
    assert FaithfulnessChecker._parse_score(raw) == 1.0


def test_pipeline_faithfulness_off_score_none():
    """faithfulness 关 → score=None。"""
    from agents_rag.citation.checker import CitationChecker
    from agents_rag.generation.context_builder import ContextBuilder
    from agents_rag.generation.llm import Generator
    from agents_rag.models import AnswerStatus, RetrievalResult
    from agents_rag.pipeline.query import QueryPipeline
    from agents_rag.retrieval.base import Retriever
    from agents_rag.retrieval.hybrid import HybridRetriever
    from agents_rag.retrieval.reranker import Reranker

    class _FakeRetriever(Retriever):
        def retrieve(self, query, k=20):
            return [RetrievalResult(
                chunk_id="a", text="GLM 支持 2048 维", score=0.9,
                retriever="vector", doc_id="d1", parent_id="p1", page=1, source_name="test.md",
            )]

    class _FakeReranker(Reranker):
        def rerank(self, query, candidates, top_n=6):
            return candidates[:top_n]

    class _FakeGenerator(Generator):
        def generate(self, query, context):
            return "回答内容[1]。"

    pipe = QueryPipeline(
        hybrid_retriever=HybridRetriever(_FakeRetriever(), _FakeRetriever()),
        reranker=_FakeReranker(),
        context_builder=ContextBuilder(parent_store=None, max_tokens=10000),
        generator=_FakeGenerator(),
        citation_checker=CitationChecker(),
        faithfulness_checker=None,  # 关
    )
    answer = pipe.ask("问题")
    assert answer.status == AnswerStatus.ANSWERED
    assert answer.faithfulness_score is None  # 未校验

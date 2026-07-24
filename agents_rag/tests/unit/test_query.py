"""查询管线测试：RRF / ContextBuilder / CitationChecker + 集成端到端。"""

from __future__ import annotations

from agents_rag.citation.checker import CitationChecker
from agents_rag.generation.context_builder import ContextBuilder
from agents_rag.generation.llm import Generator
from agents_rag.models import AnswerStatus, RetrievalResult
from agents_rag.pipeline.query import QueryPipeline
from agents_rag.retrieval.base import Retriever
from agents_rag.retrieval.hybrid import HybridRetriever, rrf_fuse
from agents_rag.retrieval.reranker import Reranker


def _result(cid, text, score=1.0, retriever="vector", doc_id="d1", parent_id="p1", page=1):
    return RetrievalResult(
        chunk_id=cid, text=text, score=score, retriever=retriever,
        doc_id=doc_id, parent_id=parent_id, page=page, source_name="test.md",
    )


# —— RRF ——
def test_rrf_fuse_consensus_ranked_first():
    vec = [_result("a", "A"), _result("b", "B")]
    bm25 = [_result("b", "B"), _result("c", "C")]
    fused = rrf_fuse(vec, bm25)
    assert fused[0].chunk_id == "b"  # 两路都靠前 → 最高
    assert {r.chunk_id for r in fused} == {"a", "b", "c"}


def test_rrf_fuse_empty():
    assert rrf_fuse([], []) == []


def test_rrf_fuse_n_lists():
    """N 路通用融合（不只 2 路）：多路共有的 chunk 排名最高。"""
    a = [_result("x", "X")]
    b = [_result("x", "X"), _result("y", "Y")]
    c = [_result("z", "Z")]
    fused = rrf_fuse(a, b, c)
    assert fused[0].chunk_id == "x"  # a、b 两路都靠前 → 最高
    assert {r.chunk_id for r in fused} == {"x", "y", "z"}


# —— ContextBuilder ——
def test_context_builder_dedup_and_numbering():
    results = [
        _result("a", "重复文本", score=0.9),
        _result("b", "重复文本", score=0.8),
        _result("c", "独特内容", score=0.7),
    ]
    cb = ContextBuilder(parent_store=None, max_tokens=10000)
    context, id_map = cb.build(results)
    assert len(id_map) == 2  # 去重
    assert "[1]" in context and "[2]" in context
    assert "test.md" in context


def test_context_builder_token_budget():
    results = [_result(f"c{i}", "x" * 100, score=1.0 - i * 0.1) for i in range(10)]
    cb = ContextBuilder(parent_store=None, max_tokens=200)
    _, id_map = cb.build(results)
    assert len(id_map) < 10  # 截断


# —— CitationChecker ——
def test_citation_checker_valid():
    id_map = {1: _result("a", "A"), 2: _result("b", "B")}
    answer = CitationChecker().check("回答[1]和[2]", id_map)
    assert answer.status == AnswerStatus.ANSWERED
    assert len(answer.citations) == 2


def test_citation_checker_invalid_marked():
    id_map = {1: _result("a", "A")}
    answer = CitationChecker().check("回答[1]和[9]", id_map)
    assert "[无效引用]" in answer.text
    assert len(answer.citations) == 1


# —— 集成 ——
class _FakeRetriever(Retriever):
    def __init__(self, results):
        self._results = results

    def retrieve(self, query, k=20):
        return self._results


class _FakeReranker(Reranker):
    def rerank(self, query, candidates, top_n=6):
        return candidates[:top_n]


class _FakeGenerator(Generator):
    def generate(self, query, context):
        return f"基于资料[1]的回答：这是结果。"


def test_query_pipeline_end_to_end():
    results = [_result("a", "GLM-4.5 支持 2048 维 embedding", score=0.9)]
    hybrid = HybridRetriever(_FakeRetriever(results), _FakeRetriever(results))
    pipe = QueryPipeline(
        hybrid_retriever=hybrid,
        reranker=_FakeReranker(),
        context_builder=ContextBuilder(parent_store=None, max_tokens=10000),
        generator=_FakeGenerator(),
        citation_checker=CitationChecker(),
    )
    answer = pipe.ask("GLM-4.5 支持多少维？")
    assert answer.status == AnswerStatus.ANSWERED
    assert "回答" in answer.text
    assert len(answer.citations) >= 1


def test_query_pipeline_empty_no_result():
    hybrid = HybridRetriever(_FakeRetriever([]), _FakeRetriever([]))
    pipe = QueryPipeline(
        hybrid_retriever=hybrid,
        reranker=_FakeReranker(),
        context_builder=ContextBuilder(parent_store=None),
        generator=_FakeGenerator(),
        citation_checker=CitationChecker(),
    )
    answer = pipe.ask("不存在的问题")
    assert answer.status == AnswerStatus.NO_RESULT
    assert "未找到" in answer.message


# —— 查询改写（双 query 融合 / 回退）——
class _RecordingRetriever(Retriever):
    """记录每次 retrieve 的 query（验证双 query 调用次数）。"""

    def __init__(self, results):
        self._results = results
        self.queries: list[str] = []

    def retrieve(self, query, k=20):
        self.queries.append(query)
        return self._results


class _FakeRewriter:
    """假 QueryRewriter：rewrite 固定返回。"""

    def __init__(self, rewritten):
        self._rewritten = rewritten

    def rewrite(self, query):
        return self._rewritten


def _make_pipe(results, rewriter=None):
    vec = _RecordingRetriever(results)
    bm25 = _RecordingRetriever(results)
    pipe = QueryPipeline(
        hybrid_retriever=HybridRetriever(vec, bm25),
        reranker=_FakeReranker(),
        context_builder=ContextBuilder(parent_store=None, max_tokens=10000),
        generator=_FakeGenerator(),
        citation_checker=CitationChecker(),
        rewriter=rewriter,
    )
    return pipe, vec


def test_pipeline_rewrite_dual_query():
    """改写开启 + 改写有效 → 原 query 与改写 query 各检索一次（双 query 融合）。"""
    pipe, vec = _make_pipe([_result("a", "内容")], rewriter=_FakeRewriter("账户密码重置"))
    pipe.ask("密码咋办")
    assert vec.queries == ["密码咋办", "账户密码重置"]


def test_pipeline_rewrite_fallback_none():
    """改写返回 None（失败）→ 仅原 query 检索一次（回退，不阻塞）。"""
    pipe, vec = _make_pipe([_result("a", "内容")], rewriter=_FakeRewriter(None))
    pipe.ask("问题")
    assert vec.queries == ["问题"]


def test_pipeline_rewrite_fallback_unchanged():
    """改写 == 原 query（LLM 判定已规范）→ 仅原 query 检索一次（省检索）。"""
    pipe, vec = _make_pipe([_result("a", "内容")], rewriter=_FakeRewriter("embedding-3 维度"))
    pipe.ask("embedding-3 维度")
    assert vec.queries == ["embedding-3 维度"]


def test_pipeline_no_rewriter_behavior_unchanged():
    """rewriter=None → 仅原 query 检索一次（行为与基线一致）。"""
    pipe, vec = _make_pipe([_result("a", "内容")], rewriter=None)
    pipe.ask("问题")
    assert vec.queries == ["问题"]

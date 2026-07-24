"""置信度聚合拒答测试：高分 ANSWERED / 低分 LOW_CONFIDENCE / faithfulness None。"""

from __future__ import annotations

from agents_rag.models import Answer, AnswerStatus, RetrievalResult


def _result(cid, text, score=0.5):
    return RetrievalResult(
        chunk_id=cid, text=text, score=score, retriever="reranked",
        doc_id="d1", parent_id="p1", page=1, source_name="test.md",
    )


def _pipe(threshold=0.5):
    from agents_rag.pipeline.query import QueryPipeline
    return QueryPipeline(
        hybrid_retriever=None, reranker=None, context_builder=None,
        generator=None, citation_checker=None,
        confidence_enabled=True, confidence_threshold=threshold,
    )


def test_high_confidence_answered():
    pipe = _pipe()
    id_map = {1: _result("a", "text", score=0.9), 2: _result("b", "text2", score=0.8)}
    answer = Answer(text="回答[1]", used_context_ids=("a",), faithfulness_score=0.9)
    result = pipe._aggregate_confidence(answer, id_map)
    assert result.status == AnswerStatus.ANSWERED
    assert result.confidence >= 0.5


def test_low_confidence_marked():
    pipe = _pipe()
    id_map = {1: _result("a", "text", score=0.1)}
    answer = Answer(text="回答", used_context_ids=(), faithfulness_score=0.1)
    result = pipe._aggregate_confidence(answer, id_map)
    assert result.status == AnswerStatus.LOW_CONFIDENCE
    assert result.confidence < 0.5


def test_faithfulness_none_excluded():
    pipe = _pipe()
    id_map = {1: _result("a", "text", score=0.9)}
    answer = Answer(text="回答[1]", used_context_ids=("a",), faithfulness_score=None)
    result = pipe._aggregate_confidence(answer, id_map)
    assert result.confidence is not None  # 两信号聚合
    assert result.status in (AnswerStatus.ANSWERED, AnswerStatus.LOW_CONFIDENCE)


def test_confidence_disabled_no_aggregation():
    """confidence 关 → 不聚合（confidence=None）。"""
    from agents_rag.pipeline.query import QueryPipeline
    pipe = QueryPipeline(
        hybrid_retriever=None, reranker=None, context_builder=None,
        generator=None, citation_checker=None,
        confidence_enabled=False,
    )
    # ask() 不调 _aggregate_confidence（confidence_enabled=False）
    # 只验证 _confidence_enabled 属性
    assert pipe._confidence_enabled is False

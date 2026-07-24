"""混合检索：向量+BM25 → RRF 融合。笔记 §3.3。

RRF 只用 rank（不用 score），规避 distance vs score 量纲相反。k=60 经验值。
"""

from __future__ import annotations

from agents_rag.models import RetrievalResult
from agents_rag.retrieval.base import Retriever

_RRF_K = 60


def rrf_fuse(
    *rankings: list[RetrievalResult],
    k: int = _RRF_K,
) -> list[RetrievalResult]:
    """RRF：对任意 N 路排名列表按排名倒数求和融合。

    不关心每路来源（vector / bm25 / 不同 query 视角均可）——只看每路排名。
    用排名不用分数，规避 distance vs score 量纲相反问题。k=60 经验值。
    """
    scores: dict[str, float] = {}
    best: dict[str, RetrievalResult] = {}

    for results in rankings:
        for rank, r in enumerate(results):
            cid = r.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in best:
                best[cid] = r

    fused_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    return [
        best[cid].model_copy(update={"score": scores[cid], "retriever": "fused"})
        for cid in fused_ids
    ]


class HybridRetriever:
    """向量+BM25 混合检索（RRF 融合）。"""

    def __init__(self, vector_retriever: Retriever, bm25_retriever: Retriever):
        self._vector = vector_retriever
        self._bm25 = bm25_retriever

    def retrieve(self, query: str, k: int = 20) -> list[RetrievalResult]:
        vec_results = self._vector.retrieve(query, k=k)
        bm25_results = self._bm25.retrieve(query, k=k)
        fused = rrf_fuse(vec_results, bm25_results)
        return fused[:k]

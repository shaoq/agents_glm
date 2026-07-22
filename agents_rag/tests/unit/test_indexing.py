"""双索引 + 父块 KV 测试。"""

from __future__ import annotations

from agents_rag.indexing.bm25_index import BM25Index
from agents_rag.indexing.chroma_store import ChromaStore
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.models import ChildChunk, ParentChunk


def _child(cid: str, doc: str, text: str, parent: str = "p0") -> ChildChunk:
    return ChildChunk(id=cid, parent_id=parent, doc_id=doc, text=text)


def test_chroma_upsert_query_delete(tmp_path):
    store = ChromaStore(tmp_path / "chroma")
    chunks = [
        _child("d1__p0__0", "d1", "hello world"),
        _child("d1__p0__1", "d1", "foo bar"),
    ]
    store.upsert(chunks, [[1.0, 0.0], [0.0, 1.0]])
    assert store.count() == 2
    res = store.query([1.0, 0.0], k=2)
    assert res and res[0][0] == "d1__p0__0"  # 最近邻
    store.delete_by_doc("d1")
    assert store.count() == 0


def test_bm25_recall_keyword():
    # 足够文档使稀有词 IDF>0（BM25 在微型 corpus 上常见词 IDF 会趋 0）
    bm = BM25Index()
    bm.upsert(
        [
            _child("d1__p0__0", "d1", "智谱GLM模型参数说明"),
            _child("d1__p0__1", "d1", "今日新闻摘要"),
            _child("d1__p0__2", "d1", "天气预报有雨"),
            _child("d1__p0__3", "d1", "股票市场下跌"),
            _child("d1__p0__4", "d1", "比赛结果公布"),
        ]
    )
    hits = bm.query("智谱GLM", k=3)
    assert hits and hits[0][0] == "d1__p0__0"


def test_bm25_chunk_id_aligned_with_chroma(tmp_path):
    chunks = [
        _child(f"d1__p{i}__0", "d1", t)
        for i, t in enumerate(
            ["向量检索方法", "关键词检索方法", "天气晴朗", "股票上涨", "比赛结束"]
        )
    ]
    bm = BM25Index()
    bm.upsert(chunks)
    st = ChromaStore(tmp_path / "c")
    st.upsert(chunks, [[float(i)] for i in range(5)])
    bm_ids = {i for i, _ in bm.query("检索", k=5)}
    assert "d1__p0__0" in bm_ids and "d1__p1__0" in bm_ids  # 含「检索」的两个
    assert st.count() == bm.count() == 5


def test_parent_store_crud(tmp_path):
    ps = ParentStore(tmp_path / "parents")
    p = ParentChunk(id="d1__p0", doc_id="d1", text="父块文本", section_path="A")
    ps.put(p)
    got = ps.get("d1", "d1__p0")
    assert got is not None and got.text == "父块文本"
    ps.delete_by_doc("d1")
    assert ps.get("d1", "d1__p0") is None


def test_three_indexes_sync_delete(tmp_path):
    doc = "d1"
    chunks = [_child("d1__p0__0", doc, "文本A"), _child("d1__p0__1", doc, "文本B")]
    st = ChromaStore(tmp_path / "c")
    st.upsert(chunks, [[1.0], [0.5]])
    bm = BM25Index()
    bm.upsert(chunks)
    ps = ParentStore(tmp_path / "p")
    ps.put(ParentChunk(id="d1__p0", doc_id=doc, text="父"))
    st.delete_by_doc(doc)
    bm.remove_by_doc(doc)
    ps.delete_by_doc(doc)
    assert st.count() == 0
    assert bm.count() == 0
    assert ps.get(doc, "d1__p0") is None

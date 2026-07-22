"""embedding 缓存 + embedder 测试。"""

from __future__ import annotations

import pytest
from tenacity import stop_after_attempt, wait_fixed

from agents_rag.indexing.cache import EmbeddingCache, cache_key
from agents_rag.indexing.embedder import Embedder, ZhipuEmbedder, _NonRetryable


class FakeEmbedder(Embedder):
    def __init__(self, dim: int = 8, max_batch: int = 2, max_concurrency: int = 1):
        self._dim = dim
        self.max_batch = max_batch
        self.max_concurrency = max_concurrency
        self.calls = 0

    @property
    def model(self) -> str:
        return "fake"

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(len(t))] * self._dim for t in texts]


# —— 6.2 缓存版本化 ——
def test_cache_key_versioned_by_model_and_dim():
    base = cache_key("a", "m1", 8)
    assert base == cache_key("a", "m1", 8)
    assert base != cache_key("a", "m2", 8)  # 换模型
    assert base != cache_key("a", "m1", 16)  # 换维度


def test_cache_put_get_versioned(tmp_path):
    c = EmbeddingCache(tmp_path / "c.sqlite")
    c.put("hello", "m1", 8, [0.1] * 8)
    assert c.get("hello", "m1", 8) == [0.1] * 8
    assert c.get("hello", "m2", 8) is None
    assert c.get("hello", "m1", 16) is None
    c.close()


def test_cache_delete_by_text(tmp_path):
    c = EmbeddingCache(tmp_path / "c.sqlite")
    c.put("hello", "m1", 8, [0.1] * 8)
    c.delete_by_text("hello", "m1", 8)
    assert c.get("hello", "m1", 8) is None
    c.close()


# —— 6.4 embedder 分批 / 缓存命中 ——
def test_embed_batches_by_max_batch():
    emb = FakeEmbedder(max_batch=2)
    vecs = emb.embed(["a", "b", "c", "d"])
    assert emb.calls == 2  # 4 文本 / 批 2 = 2 批
    assert len(vecs) == 4


def test_embed_cache_hit_skips_api(tmp_path):
    c = EmbeddingCache(tmp_path / "c.sqlite")
    emb = FakeEmbedder(max_batch=2)
    emb.embed(["a", "b"], cache=c)
    n = emb.calls
    emb.embed(["a", "b"], cache=c)  # 全命中缓存
    assert emb.calls == n  # 无新 API 调用


# —— 6.4 zhipu 重试 ——
def _make_resp(input_texts, dim):
    class D:
        def __init__(self, emb):
            self.embedding = emb

    class R:
        pass

    r = R()
    r.data = [D([0.1] * dim) for _ in input_texts]
    return r


def test_zhipu_retries_on_rate_limit():
    emb = ZhipuEmbedder(
        api_key="fake",
        dim=8,
        max_batch=64,
        retry_stop=stop_after_attempt(5),
        retry_wait=wait_fixed(0),
    )
    state = {"n": 0}

    def fake_create(**kwargs):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("429 rate limit exceeded")
        return _make_resp(kwargs["input"], 8)

    emb._client.embeddings.create = fake_create  # type: ignore[attr-defined]
    vecs = emb.embed(["a"], cache=None)
    assert state["n"] == 3  # 重试到第 3 次成功
    assert len(vecs[0]) == 8


def test_zhipu_no_retry_on_auth_error():
    emb = ZhipuEmbedder(
        api_key="fake",
        dim=8,
        retry_stop=stop_after_attempt(5),
        retry_wait=wait_fixed(0),
    )
    state = {"n": 0}

    def fake_create(**kwargs):
        state["n"] += 1
        raise RuntimeError("401 invalid api key")

    emb._client.embeddings.create = fake_create  # type: ignore[attr-defined]
    with pytest.raises(_NonRetryable):
        emb.embed(["a"], cache=None)
    assert state["n"] == 1  # 鉴权错不重试

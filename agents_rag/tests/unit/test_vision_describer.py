"""vision_describer 测试：缓存命中 / 调用并缓存 / 重试 / caption 兜底。"""

from __future__ import annotations

from tenacity import stop_after_attempt, wait_fixed

from agents_rag.indexing.vision_describer import (
    ImageDescriptionCache,
    OpenAIVisionDescriber,
)


def _fake_resp(text: str):
    class Msg:
        content = text

    class Choice:
        message = Msg()

    class R:
        choices = [Choice()]

    return R()


def test_cache_versioned_by_model(tmp_path):
    c = ImageDescriptionCache(tmp_path / "d.sqlite")
    c.put("hash1", "glm-4.5v", "描述")
    assert c.get("hash1", "glm-4.5v") == "描述"
    assert c.get("hash1", "glm-4v") is None  # 换模型不命中
    c.close()


def test_describe_cache_hit_skips_api(tmp_path):
    c = ImageDescriptionCache(tmp_path / "d.sqlite")
    c.put("hash1", "glm-4.5v", "缓存描述")
    d = OpenAIVisionDescriber(api_key="fake", base_url="http://fake/v1", model="glm-4.5v")
    called = {"n": 0}

    def fake(**kw):
        called["n"] += 1
        return _fake_resp("API描述")

    d._client.chat.completions.create = fake  # type: ignore[attr-defined]
    desc = d.describe(b"img", content_hash="hash1", cache=c)
    assert desc == "缓存描述"
    assert called["n"] == 0  # 命中缓存，没调 API
    c.close()


def test_describe_calls_api_and_caches(tmp_path):
    c = ImageDescriptionCache(tmp_path / "d.sqlite")
    d = OpenAIVisionDescriber(api_key="fake", base_url="http://fake/v1", model="glm-4.5v")
    d._client.chat.completions.create = lambda **kw: _fake_resp("API生成描述")  # type: ignore[attr-defined]
    desc = d.describe(b"img", content_hash="hash2", cache=c)
    assert desc == "API生成描述"
    assert c.get("hash2", "glm-4.5v") == "API生成描述"
    c.close()


def test_describe_retries_on_rate_limit():
    d = OpenAIVisionDescriber(
        api_key="fake", base_url="http://fake/v1", model="glm-4.5v",
        retry_stop=stop_after_attempt(4), retry_wait=wait_fixed(0),
    )
    state = {"n": 0}

    def fake(**kw):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("429 rate limit")
        return _fake_resp("成功描述")

    d._client.chat.completions.create = fake  # type: ignore[attr-defined]
    desc = d.describe(b"img", content_hash="h")
    assert state["n"] == 3
    assert desc == "成功描述"


def test_describe_failure_falls_back_to_caption():
    d = OpenAIVisionDescriber(
        api_key="fake", base_url="http://fake/v1", model="glm-4.5v",
        retry_stop=stop_after_attempt(2), retry_wait=wait_fixed(0),
    )
    d._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(RuntimeError("500"))  # type: ignore[attr-defined]
    desc = d.describe(b"img", content_hash="h", caption="图注文本")
    assert desc == "图注文本"  # caption 兜底

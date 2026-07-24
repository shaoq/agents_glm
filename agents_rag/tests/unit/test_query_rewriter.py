"""查询改写测试：产出 / strip / 空→None / 已规范不改 / retry / 异常兜底 None。"""

from __future__ import annotations

from tenacity import stop_after_attempt, wait_fixed

from agents_rag.retrieval.query_rewriter import QueryRewriter


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeClient:
    """假 OpenAI client：按 behaviors 序列返回 content 或抛异常。"""

    def __init__(self, behaviors: list) -> None:
        self._behaviors = list(behaviors)
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        b = self._behaviors.pop(0)
        if isinstance(b, Exception):
            raise b
        return _FakeResp(b)


def test_rewrite_returns_rewritten():
    r = QueryRewriter(api_key="k", base_url="u", model="m")
    r._client = _FakeClient(["账户密码重置流程"])
    assert r.rewrite("密码咋办") == "账户密码重置流程"


def test_rewrite_strips_whitespace():
    r = QueryRewriter(api_key="k", base_url="u")
    r._client = _FakeClient(["  改写结果  "])
    assert r.rewrite("问题") == "改写结果"


def test_rewrite_empty_returns_none():
    r = QueryRewriter(api_key="k", base_url="u")
    r._client = _FakeClient([""])
    assert r.rewrite("问题") is None


def test_rewrite_unchanged_returns_original():
    """LLM 判定已规范、原样输出 → 返回原 query（pipeline 层判断走原路省检索）。"""
    r = QueryRewriter(api_key="k", base_url="u")
    r._client = _FakeClient(["embedding-3 维度"])
    assert r.rewrite("embedding-3 维度") == "embedding-3 维度"


def test_rewrite_retry_then_success():
    """可重试异常（timeout）→ 重试 → 第二次成功。"""
    r = QueryRewriter(api_key="k", base_url="u", retry_wait=wait_fixed(0))
    r._client = _FakeClient([RuntimeError("connection timeout"), "改写结果"])
    assert r.rewrite("问题") == "改写结果"
    assert r._client.calls == 2


def test_rewrite_all_retry_fail_returns_none():
    """重试耗尽 → 返回 None（兜底，不向上抛）。"""
    r = QueryRewriter(
        api_key="k",
        base_url="u",
        retry_stop=stop_after_attempt(2),
        retry_wait=wait_fixed(0),
    )
    r._client = _FakeClient([RuntimeError("timeout"), RuntimeError("timeout")])
    assert r.rewrite("问题") is None


def test_rewrite_non_retryable_returns_none():
    """鉴权错误（401）→ 不重试 → 返回 None。"""
    r = QueryRewriter(api_key="k", base_url="u", retry_wait=wait_fixed(0))
    r._client = _FakeClient([RuntimeError("401 unauthorized")])
    assert r.rewrite("问题") is None
    assert r._client.calls == 1

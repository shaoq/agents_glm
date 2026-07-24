"""查询改写：Flash LLM 把口语/模糊 query 改写为检索友好。笔记 §2。

照搬 FaithfulnessChecker 的 retry 客户端模式。不编答案（区别于 HyDE，规避
领域术语偏差致命点）。异常/失败/空结果返回 None（pipeline 回退原 query，
不阻塞在线查询）。
"""

from __future__ import annotations

import logging

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_rag.indexing.embedder import _NonRetryable, _is_non_retryable

log = logging.getLogger(__name__)

_QUERY_REWRITE_PROMPT = """你是一个查询改写器。把用户的口语化或模糊问题，改写成更适合文档检索的查询。

规则：
1. 【去口语化】口语表达改成书面/文档语言（例："密码咋办"→"账户密码重置流程"）。
2. 【补术语】补充领域关键词，对齐文档可能使用的术语表述。
3. 【保留原意】不改变问题本意；不发散；不回答问题，只改写检索查询。
4. 【简洁】产出一句检索友好的查询，以关键词为主（利于关键词检索匹配）。
5. 【已规范不改】若原问题已清晰规范，原样输出即可。

只输出改写后的查询，不要解释、不要多余文字。

用户问题：{query}"""


class QueryRewriter:
    """查询改写器：Flash 把口语/模糊 query 改写为检索友好。默认关（opt-in）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "GLM-4.7-Flash",
        retry_stop=None,
        retry_wait=None,
    ):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._retry_stop = retry_stop or stop_after_attempt(4)
        self._retry_wait = retry_wait or wait_exponential(min=1, max=10)

    def rewrite(self, query: str) -> str | None:
        """返回改写后的 query；异常/失败/空结果返回 None（pipeline 回退原 query）。"""
        prompt = _QUERY_REWRITE_PROMPT.format(query=query)
        try:
            raw = self._call_api(prompt)
        except Exception as e:  # noqa: BLE001
            log.warning("查询改写失败，回退原 query: %s", e)
            return None
        rewritten = (raw or "").strip()
        return rewritten or None

    def _call_api(self, prompt: str) -> str:
        @retry(
            stop=self._retry_stop,
            wait=self._retry_wait,
            retry=retry_if_not_exception_type(_NonRetryable),
            reraise=True,
        )
        def _do() -> str:
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise

        return _do()

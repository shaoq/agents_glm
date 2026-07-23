"""Faithfulness 二次校验：LLM judge 逐句判断答案是否忠于上下文。笔记 §7.4。

照搬 OpenAIGenerator 的 retry 客户端模式。JSON 输出便于解析。
解析失败返回 None（不阻塞回答）。
"""

from __future__ import annotations

import json
import logging
import re

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_rag.indexing.embedder import _NonRetryable, _is_non_retryable

log = logging.getLogger(__name__)

_FAITHFULNESS_PROMPT = """你是一个忠实性校验器。判断以下回答的每一句是否被参考资料支撑。

参考资料：
{context}

回答：
{answer}

对回答的每一句，判断是否被参考资料直接支撑（不是推测或编造）。
输出 JSON 数组，每个元素：{{"sentence": "原句", "supported": true/false}}
仅输出 JSON，不要其他文字。"""


class FaithfulnessChecker:
    """LLM judge 逐句校验答案忠实性。默认关（opt-in）。"""

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

    def check(self, answer_text: str, context_str: str) -> float | None:
        """返回 faithfulness 分数（supported 句 / 总句）。失败返回 None。"""
        prompt = _FAITHFULNESS_PROMPT.format(context=context_str, answer=answer_text)
        try:
            raw = self._call_api(prompt)
        except Exception as e:  # noqa: BLE001
            log.warning("Faithfulness judge 失败: %s", e)
            return None
        return self._parse_score(raw)

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
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise

        return _do()

    @staticmethod
    def _parse_score(raw: str) -> float | None:
        """解析 LLM JSON 输出，算 faithfulness 分数。"""
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return None
        try:
            items = json.loads(match.group())
        except json.JSONDecodeError:
            return None
        if not items:
            return None
        supported = sum(1 for item in items if item.get("supported", False))
        return supported / len(items)

"""生成：GLM-4.5（OpenAI 兼容 chat completions）。笔记 §6。

四约束 system prompt + 低温度（0.3）+ tenacity 重试。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_rag.generation.prompts import SYSTEM_PROMPT
from agents_rag.indexing.embedder import _NonRetryable, _is_non_retryable

log = logging.getLogger(__name__)


class Generator(ABC):
    @abstractmethod
    def generate(self, query: str, context: str) -> str:
        raise NotImplementedError


class OpenAIGenerator(Generator):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "glm-4.5",
        temperature: float = 0.3,
        retry_stop=None,
        retry_wait=None,
    ):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._retry_stop = retry_stop or stop_after_attempt(4)
        self._retry_wait = retry_wait or wait_exponential(min=1, max=10)

    def generate(self, query: str, context: str) -> str:
        prompt = SYSTEM_PROMPT.format(context=context, query=query)

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
                    temperature=self._temperature,
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                if _is_non_retryable(e):
                    raise _NonRetryable(str(e)) from e
                raise

        return _do()

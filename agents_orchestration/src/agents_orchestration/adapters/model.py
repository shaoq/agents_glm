"""OpenAI-compatible Model Adapter (task 7.7) and model profile routing (6.7).

The adapter reads the secret (``api_key``) ONLY at this boundary, from the
:class:`ModelProfile` supplied by Settings; it is never written into prompts,
state, events, checkpoints, artifacts or logs (design Decision 12 / task 12.4).
``openai`` is imported lazily. Model-profile routing lives in
:func:`agents_orchestration.adapters.base.select_model_profile`.
"""

from __future__ import annotations

from agents_orchestration.adapters.base import (
    AsyncCapabilityAdapter,
    ModelProfile,
    descriptor_for,
    to_async,
)
from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import CapabilityKind, FailureCode
from agents_orchestration.domain.evidence import SourceIdentity, SourceKind, Usage


class OpenAIModelAdapter(AsyncCapabilityAdapter):
    def __init__(self, profile: ModelProfile, *, descriptor=None) -> None:
        super().__init__(descriptor or descriptor_for(CapabilityKind.MODEL, "model::openai"))
        self.profile = profile

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        op = f"op::{request.request_id}"
        prompt = str(request.inputs.get("prompt", ""))
        try:
            text, tokens = await self._complete(prompt)
        except TimeoutError:
            return CapabilityResult.failed(
                operation_id=op, failure_code=FailureCode.TIMEOUT, retryable=True
            )
        except Exception:  # noqa: BLE001 - retry-safe diagnostics, secret never logged
            return CapabilityResult.failed(
                operation_id=op, failure_code=FailureCode.UPSTREAM_ERROR, retryable=True
            )
        return CapabilityResult.ok(
            operation_id=op,
            data={"text": text},
            source=SourceIdentity(
                source_id=f"model:{self.profile.name}", source_kind=SourceKind.MODEL
            ),
            usage=Usage(tokens=tokens),
        )

    async def _complete(self, prompt: str) -> tuple[str, int]:
        import openai  # lazy: not required at package import time

        client = openai.OpenAI(
            api_key=self.profile.api_key,
            base_url=self.profile.base_url,
            timeout=self.profile.timeout_seconds,
        )
        response = await to_async(
            client.chat.completions.create,
            model=self.profile.name,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""
        tokens = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
        return text, tokens

    async def invoke_tools(self, request: CapabilityRequest, tools: list[dict]) -> CapabilityResult:
        """Function-calling mode: pass tool definitions, parse the first tool_call.

        Returns ``CapabilityResult.data = {"tool_name", "arguments"}`` where
        ``arguments`` is the raw JSON string from the model (callers validate via
        Pydantic). Plain-text :meth:`invoke` is retained for long-form output.
        """

        op = f"op::{request.request_id}"
        prompt = str(request.inputs.get("prompt", ""))
        try:
            name, args, tokens = await self._complete_with_tools(prompt, tools)
        except TimeoutError:
            return CapabilityResult.failed(
                operation_id=op, failure_code=FailureCode.TIMEOUT, retryable=True
            )
        except Exception:  # noqa: BLE001 - retry-safe diagnostics, secret never logged
            return CapabilityResult.failed(
                operation_id=op, failure_code=FailureCode.UPSTREAM_ERROR, retryable=True
            )
        if not name:
            # Model returned no tool call -> treat as invalid (non-retryable) so the
            # phase degrades to IDLE rather than silently fabricating an output.
            return CapabilityResult.failed(
                operation_id=op, failure_code=FailureCode.INVALID_RESPONSE, retryable=False
            )
        return CapabilityResult.ok(
            operation_id=op,
            data={"tool_name": name, "arguments": args or ""},
            source=SourceIdentity(
                source_id=f"model:{self.profile.name}", source_kind=SourceKind.MODEL
            ),
            usage=Usage(tokens=tokens),
        )

    async def _complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> tuple[str | None, str | None, int]:
        import openai  # lazy: not required at package import time

        client = openai.OpenAI(
            api_key=self.profile.api_key,
            base_url=self.profile.base_url,
            timeout=self.profile.timeout_seconds,
        )
        response = await to_async(
            client.chat.completions.create,
            model=self.profile.name,
            messages=[{"role": "user", "content": prompt}],
            tools=tools,
            tool_choice="auto",
        )
        msg = response.choices[0].message
        tokens = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return None, None, tokens
        first = tool_calls[0]
        return first.function.name, first.function.arguments, tokens


__all__ = ["OpenAIModelAdapter", "ModelProfile"]

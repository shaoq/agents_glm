"""Recall intent construction with deterministic fallback.

The intent builder turns a ``RecallRequest`` into a structured ``RecallIntent``
using an LLM for semantic understanding, while identity, authorization and
explicit constraints stay under deterministic control. On any LLM failure
(timeout, malformed output, low confidence) it falls back to a conservative
intent built from the raw query and explicit constraints, and reports
``intent_fallback`` via diagnostics.

Reference: design 5.1-5.4 (intent construction and degraded intent).
"""

import json
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents_memory.models import MemoryType
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    DegradationCode,
    ExplicitConstraints,
    QueryVariant,
    RecallIntent,
    RecallRequest,
    TemporalIntent,
)
from agents_memory.recall.prompts import RECALL_INTENT_SYSTEM_PROMPT

_LLM_RETRY_TYPES = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

# Connection/timeout failures that may be raised by a non-OpenAI client or by
# the network stack; treated as recoverable LLM unavailability.
_NETWORK_FAILURE_TYPES = (TimeoutError, ConnectionError, OSError)


class IntentBuildError(ValueError):
    """Raised when the LLM intent output cannot be produced or parsed."""


class _IntentLLMOutput(BaseModel):
    primary_query: str
    purpose: str = "general_recovery"
    query_variants: list[str] = []
    target_memory_types: list[MemoryType] = []
    temporal_need: TemporalIntent | None = None
    subject_hints: list[str] = []
    relationship_need: bool = False
    confidence: float = 0.0

    @field_validator("primary_query")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("primary_query must not be blank")
        return stripped


def build_fallback_intent(request: RecallRequest) -> RecallIntent:
    """Conservative intent from the raw query and explicit constraints only.

    Per design 5.4: never narrow the legal candidate range on failure.
    """

    explicit = ExplicitConstraints(
        types=request.explicit_types,
        time_range=request.explicit_time_range,
        temporal_intent=request.temporal_intent,
    )
    return RecallIntent(
        primary_query=request.query,
        purpose="general_recovery",
        query_variants=(QueryVariant(text=request.query, purpose="original"),),
        target_memory_types=request.explicit_types or (MemoryType.FACT, MemoryType.EVENT),
        temporal_need=request.temporal_intent,
        explicit_constraints=explicit,
        confidence=0.0,
        fallback=True,
    )


class GLMIntentBuilder:
    """LLM-backed intent builder (GLM-4.7-Flash compatible)."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        max_variants: int = 3,
        min_confidence: float = 0.5,
    ) -> None:
        self.client = client
        self.model = model
        self.max_variants = max_variants
        self.min_confidence = min_confidence

    def build(self, request: RecallRequest, diag: RecallDiagnostics) -> RecallIntent:
        try:
            output = self._invoke(request)
        except IntentBuildError as exc:
            diag.degrade(DegradationCode.INTENT_FALLBACK, str(exc))
            return build_fallback_intent(request)
        if output.confidence < self.min_confidence:
            diag.degrade(DegradationCode.INTENT_FALLBACK, "low confidence")
            return build_fallback_intent(request)
        return self._normalize(request, output)

    @retry(
        retry=retry_if_exception_type(_LLM_RETRY_TYPES),
        wait=wait_exponential(min=0.01, max=1),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    def _invoke(self, request: RecallRequest) -> _IntentLLMOutput:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": RECALL_INTENT_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(self._payload(request), ensure_ascii=False),
                    },
                ],
            )
            content = response.choices[0].message.content
            return _IntentLLMOutput.model_validate_json(content)
        except _LLM_RETRY_TYPES as exc:
            raise IntentBuildError(f"llm unavailable: {type(exc).__name__}") from exc
        except _NETWORK_FAILURE_TYPES as exc:
            raise IntentBuildError(f"network: {type(exc).__name__}") from exc
        except (ValidationError, ValueError, TypeError, IndexError) as exc:
            raise IntentBuildError("invalid intent output") from exc

    @staticmethod
    def _payload(request: RecallRequest) -> dict[str, object]:
        return {
            "query": request.query,
            "purpose": request.purpose,
            "recent_messages": [
                {"role": m.role, "content": m.content} for m in request.recent_messages
            ],
            "explicit_types": [t.value for t in request.explicit_types],
            "explicit_temporal_intent": request.temporal_intent.value
            if request.temporal_intent
            else None,
        }

    def _normalize(self, request: RecallRequest, output: _IntentLLMOutput) -> RecallIntent:
        explicit = ExplicitConstraints(
            types=request.explicit_types,
            time_range=request.explicit_time_range,
            temporal_intent=request.temporal_intent,
        )
        target_types = (
            tuple(request.explicit_types)
            if request.explicit_types
            else (tuple(output.target_memory_types) or (MemoryType.FACT, MemoryType.EVENT))
        )
        temporal_need = request.temporal_intent or output.temporal_need
        variants: list[str] = [output.primary_query]
        for variant in output.query_variants:
            stripped = variant.strip()
            if stripped and stripped not in variants:
                variants.append(stripped)
            if len(variants) >= self.max_variants:
                break
        return RecallIntent(
            primary_query=output.primary_query,
            purpose=output.purpose or "general_recovery",
            query_variants=tuple(QueryVariant(text=v, purpose="llm") for v in variants),
            target_memory_types=target_types,
            temporal_need=temporal_need,
            subject_hints=tuple(output.subject_hints),
            relationship_need=output.relationship_need,
            explicit_constraints=explicit,
            confidence=output.confidence,
            fallback=False,
        )


class FallbackIntentBuilder:
    """Always-fallback intent builder for deterministic or unconfigured runs."""

    def build(self, request: RecallRequest, diag: RecallDiagnostics) -> RecallIntent:
        diag.degrade(DegradationCode.INTENT_FALLBACK, "fallback-only builder")
        return build_fallback_intent(request)

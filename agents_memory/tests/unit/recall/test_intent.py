"""Tests for GLMIntentBuilder: structured extraction, explicit-vs-inferred and
fallback semantics (task 3.1/3.3/3.4).
"""

import json
from types import SimpleNamespace

from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.intent import (
    FallbackIntentBuilder,
    GLMIntentBuilder,
    build_fallback_intent,
)
from agents_memory.recall.models import (
    DegradationCode,
    MemoryType,
    RecallIntent,
    RecallRequest,
    TemporalIntent,
)


class _FakeClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error

    @property
    def chat(self) -> "_FakeClient":
        return self

    @property
    def completions(self) -> "_FakeClient":
        return self

    def create(self, **kwargs):  # noqa: ARG002
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


_VALID_INTENT_JSON = json.dumps(
    {
        "primary_query": "current decision on auth",
        "purpose": "recover_decision",
        "query_variants": ["auth decision", "login choice"],
        "target_memory_types": ["fact"],
        "temporal_need": "current_state",
        "subject_hints": ["auth"],
        "relationship_need": False,
        "confidence": 0.8,
    }
)


def _request(**kwargs) -> RecallRequest:
    defaults: dict = {"user_id": "u1", "agent_id": "a1", "session_id": "s1", "query": "q"}
    defaults.update(kwargs)
    return RecallRequest(**defaults)


class TestStructuredIntentExtraction:
    def test_builds_structured_intent(self):
        builder = GLMIntentBuilder(client=_FakeClient(content=_VALID_INTENT_JSON), model="m")
        diag = RecallDiagnostics()
        intent = builder.build(_request(), diag)
        assert intent.primary_query == "current decision on auth"
        assert intent.purpose == "recover_decision"
        assert MemoryType.FACT in intent.target_memory_types
        assert intent.temporal_need is TemporalIntent.CURRENT_STATE
        assert intent.confidence == 0.8
        assert intent.fallback is False
        assert diag.degradations == ()

    def test_query_variants_carry_primary_plus_extras(self):
        builder = GLMIntentBuilder(client=_FakeClient(content=_VALID_INTENT_JSON), model="m")
        intent = builder.build(_request(), RecallDiagnostics())
        texts = [v.text for v in intent.query_variants]
        assert texts[0] == "current decision on auth"
        assert "auth decision" in texts

    def test_bounded_query_variants(self):
        payload = json.dumps(
            {
                "primary_query": "q",
                "query_variants": ["a", "b", "c", "d"],
                "target_memory_types": ["fact"],
                "confidence": 0.9,
            }
        )
        builder = GLMIntentBuilder(client=_FakeClient(content=payload), model="m", max_variants=2)
        intent = builder.build(_request(), RecallDiagnostics())
        assert len(intent.query_variants) <= 2


class TestExplicitConstraintsNotOverridden:
    def test_explicit_types_override_llm_inference(self):
        builder = GLMIntentBuilder(client=_FakeClient(content=_VALID_INTENT_JSON), model="m")
        intent = builder.build(_request(explicit_types=(MemoryType.EVENT,)), RecallDiagnostics())
        assert intent.target_memory_types == (MemoryType.EVENT,)
        assert intent.explicit_constraints.types == (MemoryType.EVENT,)

    def test_explicit_temporal_overrides_llm(self):
        builder = GLMIntentBuilder(client=_FakeClient(content=_VALID_INTENT_JSON), model="m")
        intent = builder.build(
            _request(temporal_intent=TemporalIntent.POINT_IN_TIME), RecallDiagnostics()
        )
        assert intent.temporal_need is TemporalIntent.POINT_IN_TIME

    def test_identity_not_sent_to_llm(self):
        captured: dict = {}

        class _CapturingClient(_FakeClient):
            def create(self, **kwargs):
                captured["payload"] = json.loads(kwargs["messages"][1]["content"])
                return super().create(**kwargs)

        builder = GLMIntentBuilder(client=_CapturingClient(content=_VALID_INTENT_JSON), model="m")
        builder.build(_request(), RecallDiagnostics())
        assert "user_id" not in captured["payload"]
        assert "agent_id" not in captured["payload"]


class TestIntentFallback:
    def test_timeout_falls_back(self):
        builder = GLMIntentBuilder(client=_FakeClient(error=TimeoutError("slow")), model="m")
        diag = RecallDiagnostics()
        intent = builder.build(_request(), diag)
        assert intent.fallback is True
        assert DegradationCode.INTENT_FALLBACK in diag.degradations

    def test_malformed_output_falls_back(self):
        builder = GLMIntentBuilder(client=_FakeClient(content="not json"), model="m")
        diag = RecallDiagnostics()
        intent = builder.build(_request(), diag)
        assert intent.fallback is True
        assert DegradationCode.INTENT_FALLBACK in diag.degradations

    def test_low_confidence_falls_back(self):
        payload = json.dumps(
            {"primary_query": "q", "target_memory_types": ["fact"], "confidence": 0.2}
        )
        builder = GLMIntentBuilder(client=_FakeClient(content=payload), model="m")
        diag = RecallDiagnostics()
        intent = builder.build(_request(), diag)
        assert intent.fallback is True

    def test_fallback_preserves_original_query(self):
        request = _request(query="what did I decide")
        intent = build_fallback_intent(request)
        assert intent.primary_query == "what did I decide"
        assert intent.fallback is True
        assert intent.query_variants[0].text == "what did I decide"

    def test_fallback_intent_defaults_types_when_unspecified(self):
        intent = build_fallback_intent(RecallRequest(user_id="u1", query="q"))
        assert MemoryType.FACT in intent.target_memory_types
        assert MemoryType.EVENT in intent.target_memory_types

    def test_fallback_only_builder_always_degrades(self):
        builder = FallbackIntentBuilder()
        diag = RecallDiagnostics()
        intent: RecallIntent = builder.build(_request(), diag)
        assert intent.fallback is True
        assert DegradationCode.INTENT_FALLBACK in diag.degradations

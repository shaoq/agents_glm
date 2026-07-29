"""Unit tests for fake multi-source doubles (task 1.3)."""

from __future__ import annotations

import pytest

from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.evidence import SourceKind
from tests.support.multi_source_doubles import (
    FakeMultiSourceAdapter,
    fake_memory_adapter,
    fake_rag_adapter,
    fake_web_adapter,
)


def _request(kind: CapabilityKind, *, query: str = "AI agent trends") -> CapabilityRequest:
    return CapabilityRequest(
        request_id="req-1",
        capability_id=f"cap::{kind.value}",
        worker_id="worker::evidence_researcher",
        run_id="run-1",
        task_id="task-1",
        attempt_id="att-1",
        inputs={"query": query},
    )


@pytest.mark.parametrize(
    "factory,kind,source_kind,untrusted",
    [
        (fake_rag_adapter, CapabilityKind.RAG_SEARCH, SourceKind.RAG, False),
        (fake_memory_adapter, CapabilityKind.MEMORY_RECALL, SourceKind.MEMORY, False),
        (fake_web_adapter, CapabilityKind.WEB_RESEARCH, SourceKind.WEB, True),
    ],
)
@pytest.mark.asyncio
async def test_fake_adapter_returns_correct_source(factory, kind, source_kind, untrusted):
    adapter: FakeMultiSourceAdapter = factory()
    result = await adapter.invoke(_request(kind))

    assert result.status.value == "ok"
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert ev.source.source_kind is source_kind
    assert ev.is_untrusted is untrusted
    assert adapter.kind is kind


@pytest.mark.asyncio
async def test_fake_adapter_accepts_query_input():
    adapter = fake_rag_adapter()
    result = await adapter.invoke(_request(CapabilityKind.RAG_SEARCH, query="glm-5.2"))
    assert "glm-5.2" in result.evidence[0].content_text


@pytest.mark.asyncio
async def test_fake_adapter_fail_lane_returns_failed():
    adapter = fake_rag_adapter(fail=True)
    result = await adapter.invoke(_request(CapabilityKind.RAG_SEARCH))
    assert result.status.value == "failed"
    assert result.evidence == ()


def test_fake_double_module_does_not_import_real_sibling_or_network():
    """Architecture (task 1.3): doubles must not pull real sibling / network stacks."""

    import tests.support.multi_source_doubles as mod

    source = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("import httpx", "import openai", "agents_rag", "agents_memory"):
        assert forbidden not in source, f"forbidden import found in doubles: {forbidden}"

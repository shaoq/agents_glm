"""Deterministic Fake Memory/RAG/Web/Model adapters (task 7.1).

These back the default (offline) test suite and the Fake-based E2E (13.1). They
never touch a sibling project, the network, the filesystem or ``.env``; outputs
are a pure function of the request so runs are fully reproducible.
"""

from __future__ import annotations

from agents_orchestration.adapters.base import AsyncCapabilityAdapter, descriptor_for
from agents_orchestration.capabilities.registry import CapabilityRegistry
from agents_orchestration.domain.capability import (
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
)
from agents_orchestration.domain.enums import CapabilityKind, FailureCode
from agents_orchestration.domain.evidence import (
    Evidence,
    SourceIdentity,
    SourceKind,
    Usage,
)


def _operation_id(request: CapabilityRequest) -> str:
    return f"op::{request.request_id}"


class FakeMemoryAdapter(AsyncCapabilityAdapter):
    def __init__(self, evidence: tuple[Evidence, ...] | None = None) -> None:
        super().__init__(descriptor_for(CapabilityKind.MEMORY_RECALL, "fake::memory"))
        self._evidence = evidence or (
            Evidence(
                evidence_id="mem-1",
                source=SourceIdentity(
                    source_id="mem:s1", source_kind=SourceKind.MEMORY, uri="memory:s1"
                ),
                content_text="remembered fact",
                trust=0.6,
            ),
        )

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        query = str(request.inputs.get("query", ""))
        return CapabilityResult.ok(
            operation_id=_operation_id(request),
            evidence=self._evidence,
            source=SourceIdentity(source_id="mem:s1", source_kind=SourceKind.MEMORY),
            usage=Usage(tokens=20),
            citation=None,
            data={"query": query},
        )


class FakeRAGAdapter(AsyncCapabilityAdapter):
    def __init__(self, evidence: tuple[Evidence, ...] | None = None) -> None:
        super().__init__(descriptor_for(CapabilityKind.RAG_SEARCH, "fake::rag"))
        self._evidence = evidence or (
            Evidence(
                evidence_id="rag-1",
                source=SourceIdentity(source_id="rag:s1", source_kind=SourceKind.RAG, uri="kb:s1"),
                content_text="knowledge passage",
                citation="kb:s1",
                trust=0.8,
            ),
        )

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult.ok(
            operation_id=_operation_id(request),
            evidence=self._evidence,
            source=SourceIdentity(source_id="rag:s1", source_kind=SourceKind.RAG),
            citation="kb:s1",
            usage=Usage(tokens=40),
        )


class FakeWebAdapter(AsyncCapabilityAdapter):
    """Web is disabled by default; only returns content when explicitly enabled."""

    def __init__(
        self, *, enabled: bool = False, evidence: tuple[Evidence, ...] | None = None
    ) -> None:
        super().__init__(descriptor_for(CapabilityKind.WEB_RESEARCH, "fake::web"))
        self.enabled = enabled
        self._evidence = evidence or (
            Evidence(
                evidence_id="web-1",
                source=SourceIdentity(
                    source_id="web:s1", source_kind=SourceKind.WEB, uri="https://example.com/s1"
                ),
                content_text="web passage",
                citation="https://example.com/s1",
                trust=0.5,
            ),
        )

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        if not self.enabled:
            return CapabilityResult.failed(
                operation_id=_operation_id(request),
                failure_code=FailureCode.UNAUTHORIZED,
            )
        return CapabilityResult.ok(
            operation_id=_operation_id(request),
            evidence=self._evidence,
            source=SourceIdentity(
                source_id="web:s1", source_kind=SourceKind.WEB, uri="https://example.com/s1"
            ),
            citation="https://example.com/s1",
            usage=Usage(tokens=60),
        )


class FakeModelAdapter(AsyncCapabilityAdapter):
    def __init__(self, text: str = "model completion") -> None:
        super().__init__(descriptor_for(CapabilityKind.MODEL, "fake::model"))
        self.text = text

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        prompt = str(request.inputs.get("prompt", ""))
        return CapabilityResult.ok(
            operation_id=_operation_id(request),
            data={"text": self.text, "prompt": prompt},
            source=SourceIdentity(source_id="model:fake", source_kind=SourceKind.MODEL),
            usage=Usage(tokens=len(prompt.split()) + 10),
        )


def build_fake_registry(*, web_enabled: bool = False) -> CapabilityRegistry:
    """Register the four deterministic Fake adapters (web disabled unless asked)."""

    registry = CapabilityRegistry()
    registry.register(FakeMemoryAdapter().descriptor, FakeMemoryAdapter())
    registry.register(FakeRAGAdapter().descriptor, FakeRAGAdapter())
    registry.register(
        FakeWebAdapter(enabled=web_enabled).descriptor, FakeWebAdapter(enabled=web_enabled)
    )
    registry.register(FakeModelAdapter().descriptor, FakeModelAdapter())
    return registry


__all__ = [
    "FakeMemoryAdapter",
    "FakeRAGAdapter",
    "FakeWebAdapter",
    "FakeModelAdapter",
    "build_fake_registry",
    "CapabilityDescriptor",
]

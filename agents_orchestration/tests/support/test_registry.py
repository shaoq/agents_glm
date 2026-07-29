"""Real-adapter test registry: replaces the deleted Fake registry.

Memory/RAG/Web adapters are constructed with deterministic injected functions
(the adapters' designed test seam — ``recall_fn`` / ``query_fn`` / ``fetch_fn``);
no Fake classes survive. The MODEL capability is intentionally absent: production
does not route MODEL through the capability registry (it goes via the LLM ports),
so the test registry need not cover it.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents_orchestration.adapters.base import descriptor_for
from agents_orchestration.adapters.memory import MemoryRecallAdapter
from agents_orchestration.adapters.rag import RagAdapter
from agents_orchestration.adapters.web import WebResearchAdapter
from agents_orchestration.capabilities.registry import CapabilityRegistry
from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.evidence import Evidence, SourceIdentity, SourceKind


def _memory_evidence(query: str, scope: str | None) -> Iterable[Evidence]:
    return (
        Evidence(
            evidence_id="mem-1",
            source=SourceIdentity(
                source_id="mem:s1", source_kind=SourceKind.MEMORY, uri="memory:s1"
            ),
            content_text="remembered fact",
            trust=0.6,
        ),
    )


def _rag_evidence(query: str) -> tuple[Iterable[Evidence], str | None]:
    return (
        (
            Evidence(
                evidence_id="rag-1",
                source=SourceIdentity(source_id="rag:s1", source_kind=SourceKind.RAG, uri="kb:s1"),
                content_text="knowledge passage",
                citation="kb:s1",
                trust=0.8,
            ),
        ),
        "kb:s1",
    )


def _web_evidence(url: str) -> Iterable[Evidence]:
    return (
        Evidence(
            evidence_id="web-1",
            source=SourceIdentity(source_id="web:s1", source_kind=SourceKind.WEB, uri=url),
            content_text="web passage",
            citation=url,
            trust=0.5,
        ),
    )


def build_test_registry() -> CapabilityRegistry:
    """Register real Memory/RAG/Web adapters with deterministic injected fns.

    The Web switch is not a registry concern — it is enforced by the
    ``CapabilityRouter`` via Run Policy (``web_enabled`` / ``web_allowed_domains``).
    """

    registry = CapabilityRegistry()
    memory = MemoryRecallAdapter(
        _memory_evidence, descriptor=descriptor_for(CapabilityKind.MEMORY_RECALL, "memory")
    )
    rag = RagAdapter(_rag_evidence, descriptor=descriptor_for(CapabilityKind.RAG_SEARCH, "rag"))
    web = WebResearchAdapter(
        _web_evidence, descriptor=descriptor_for(CapabilityKind.WEB_RESEARCH, "web")
    )
    registry.register(memory.descriptor, memory)
    registry.register(rag.descriptor, rag)
    registry.register(web.descriptor, web)
    return registry

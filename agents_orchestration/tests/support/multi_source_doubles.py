"""Fake multi-source capability doubles for tests.

Deterministic ``AsyncCapabilityAdapter`` doubles (RAG / Memory / Web) that return
preset normalized ``Evidence``, so the multi-source research handler can be
exercised end-to-end through the real ``CapabilityRouter`` without touching
sibling services.

Lives under ``tests/`` (not production code) per ``remove-offline-fake-assembly``:
production code contains no Fake classes; these doubles inject only via the
``build_production_coordinator`` explicit-port seam.
"""

from __future__ import annotations

from agents_orchestration.adapters.base import AsyncCapabilityAdapter, descriptor_for
from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import CapabilityKind, FailureCode
from agents_orchestration.domain.evidence import Evidence, SourceIdentity, SourceKind, Usage


def _preset_evidence(
    source_kind: SourceKind,
    request_id: str,
    *,
    query: str,
    trust: float = 0.6,
    untrusted: bool = False,
) -> Evidence:
    """Build one normalized Evidence carrying the source identity / trust / untrusted label."""

    return Evidence(
        evidence_id=f"{source_kind.value}-{request_id}",
        source=SourceIdentity(
            source_id=f"{source_kind.value}-preset",
            source_kind=source_kind,
            uri=f"{source_kind.value}://preset/{request_id}",
            trust=trust,
        ),
        content_text=f"[{source_kind.value}] preset evidence for: {query or '(no query)'}",
        citation=f"{source_kind.value}://preset",
        trust=trust,
        is_untrusted=untrusted,
    )


class FakeMultiSourceAdapter(AsyncCapabilityAdapter):
    """Returns a preset Evidence tuple for its capability kind.

    Accepts a ``query`` input (fake web accepts query too, bypassing the
    query→url conversion that real web needs). ``fail=True`` makes the lane fail
    so JoinPolicy degradation paths can be exercised.
    """

    def __init__(
        self,
        kind: CapabilityKind,
        source_kind: SourceKind,
        *,
        untrusted: bool = False,
        fail: bool = False,
        tokens: int = 20,
    ) -> None:
        super().__init__(descriptor_for(kind))
        self._kind = kind
        self._source_kind = source_kind
        self._untrusted = untrusted
        self._fail = fail
        self._tokens = tokens

    @property
    def kind(self) -> CapabilityKind:
        return self._kind

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        op = f"op::{request.request_id}"
        if self._fail:
            return CapabilityResult.failed(
                operation_id=op, failure_code=FailureCode.UPSTREAM_ERROR, retryable=False
            )
        query = str(request.inputs.get("query", ""))
        ev = _preset_evidence(
            self._source_kind, request.request_id, query=query, untrusted=self._untrusted
        )
        return CapabilityResult.ok(
            operation_id=op,
            evidence=(ev,),
            source=ev.source,
            citation=ev.citation,
            usage=Usage(tokens=self._tokens),
        )


def fake_rag_adapter(**kwargs: object) -> FakeMultiSourceAdapter:
    return FakeMultiSourceAdapter(CapabilityKind.RAG_SEARCH, SourceKind.RAG, **kwargs)


def fake_memory_adapter(**kwargs: object) -> FakeMultiSourceAdapter:
    return FakeMultiSourceAdapter(CapabilityKind.MEMORY_RECALL, SourceKind.MEMORY, **kwargs)


def fake_web_adapter(**kwargs: object) -> FakeMultiSourceAdapter:
    # Web content is always untrusted (design Decision 12) — never a control instruction.
    return FakeMultiSourceAdapter(
        CapabilityKind.WEB_RESEARCH, SourceKind.WEB, untrusted=True, **kwargs
    )


def build_fake_multi_source_registry(*, web: bool = True):
    """Register the fake RAG / Memory / (Web) adapters into a CapabilityRegistry.

    Returns the registry so tests can hand it to ``build_production_coordinator``.
    Web is included only when the caller intends to exercise the web lane.
    """

    from agents_orchestration.capabilities.registry import CapabilityRegistry

    registry = CapabilityRegistry()
    adapters = [fake_rag_adapter(), fake_memory_adapter()]
    if web:
        adapters.append(fake_web_adapter())
    for adapter in adapters:
        registry.register(adapter.descriptor, adapter)
    return registry

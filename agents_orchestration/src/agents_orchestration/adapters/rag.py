"""RAG Adapter (tasks 7.4 / 7.5).

Wraps the public ``QueryPipeline`` / RAG service boundary. The adapter preserves
citations, sources, confidence/sufficiency and degradation fields in the
normalized ``CapabilityResult`` (task 7.5); ``agents_rag`` is imported lazily by
the supplied factory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from agents_orchestration.adapters.base import AsyncCapabilityAdapter, descriptor_for, to_async
from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.domain.evidence import (
    Degradation,
    Evidence,
    SourceIdentity,
    SourceKind,
    Usage,
)


class RagAdapter(AsyncCapabilityAdapter):
    def __init__(self, query_fn, *, descriptor=None) -> None:
        super().__init__(descriptor or descriptor_for(CapabilityKind.RAG_SEARCH, "rag::search"))
        self._query_fn = query_fn

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        op = f"op::{request.request_id}"
        query = str(request.inputs.get("query", ""))
        try:
            evidences, citation = await to_async(self._query_fn, query)
        except Exception as exc:  # noqa: BLE001 - degrade explicitly
            return CapabilityResult.degraded(
                operation_id=op,
                degradation=(
                    Degradation(flag="rag_unavailable", reason=str(exc), fallback_used=False),
                ),
            )
        if not evidences:
            return CapabilityResult.degraded(
                operation_id=op,
                degradation=(
                    Degradation(
                        flag="rag_empty", reason="local knowledge unavailable", fallback_used=False
                    ),
                ),
            )
        return CapabilityResult.ok(
            operation_id=op,
            evidence=tuple(evidences),
            source=SourceIdentity(source_id="rag", source_kind=SourceKind.RAG),
            citation=citation,
            usage=Usage(tokens=40),
        )


def query_fn_from_rag_pipeline(pipeline) -> Callable[[str], tuple[Iterable[Evidence], str | None]]:
    """Bind a RAG ``QueryPipeline`` to the ``query_fn(query)`` boundary (7.4)."""

    def _query(query: str) -> tuple[Iterable[Evidence], str | None]:
        result = pipeline.query(query=query)
        evidences = tuple(getattr(result, "evidence", ()) or ())
        citation = getattr(result, "citation", None)
        return evidences, citation

    return _query

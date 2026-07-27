"""Memory Recall Adapter (tasks 7.2 / 7.3).

Wraps the public ``MemoryService`` boundary behind the async Capability Port. The
adapter is constructed with a ``recall_fn`` that already maps MemoryService output
into normalized ``Evidence`` (carrying scope, sufficiency, conflict and
degradation fields, task 7.3); it provides the bounded async bridge (task 7.8)
and degrades explicitly rather than faking success (design Decision 13).

``agents_memory`` is imported lazily inside the supplied factory so importing this
module never requires the sibling to be installed.
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


class MemoryRecallAdapter(AsyncCapabilityAdapter):
    def __init__(self, recall_fn, *, descriptor=None) -> None:
        super().__init__(
            descriptor or descriptor_for(CapabilityKind.MEMORY_RECALL, "memory::recall")
        )
        self._recall_fn = recall_fn

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        op = f"op::{request.request_id}"
        query = str(request.inputs.get("query", ""))
        scope = request.data_scope
        try:
            evidences = await to_async(self._recall_fn, query, scope)
        except Exception as exc:  # noqa: BLE001 - degrade explicitly
            return CapabilityResult.degraded(
                operation_id=op,
                degradation=(
                    Degradation(flag="memory_unavailable", reason=str(exc), fallback_used=False),
                ),
            )
        if not evidences:
            return CapabilityResult.degraded(
                operation_id=op,
                degradation=(
                    Degradation(
                        flag="memory_empty", reason="no personalized context", fallback_used=False
                    ),
                ),
            )
        return CapabilityResult.ok(
            operation_id=op,
            evidence=tuple(evidences),
            source=SourceIdentity(source_id="memory", source_kind=SourceKind.MEMORY),
            usage=Usage(tokens=20),
        )


def recall_fn_from_memory_service(service) -> Callable[[str, str | None], Iterable[Evidence]]:
    """Bind a ``MemoryService`` to the ``recall_fn(query, scope)`` boundary (7.2).

    Only the public service boundary is touched here; the sibling import happens
    wherever ``service`` is constructed (composition root), not in this package.
    """

    def _recall(query: str, scope: str | None) -> Iterable[Evidence]:
        result = service.recall(query=query, scope=scope) if scope else service.recall(query=query)
        return tuple(getattr(result, "evidence", ()) or ())

    return _recall

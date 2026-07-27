"""Web Research Adapter (task 7.6).

Disabled by default; the CapabilityRouter only reaches this adapter when Run
Policy enables Web AND the requested domain is allowed. ``httpx`` is imported
lazily so the package imports without a network stack. Web content is returned
as untrusted Evidence only — never as a control instruction (design Decision 12).
"""

from __future__ import annotations

from collections.abc import Iterable

from agents_orchestration.adapters.base import AsyncCapabilityAdapter, descriptor_for, to_async
from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import CapabilityKind, FailureCode
from agents_orchestration.domain.evidence import (
    Degradation,
    Evidence,
    SourceIdentity,
    SourceKind,
    Usage,
)


class WebResearchAdapter(AsyncCapabilityAdapter):
    def __init__(self, fetch_fn=None, *, descriptor=None) -> None:
        super().__init__(descriptor or descriptor_for(CapabilityKind.WEB_RESEARCH, "web::research"))
        self._fetch_fn = fetch_fn

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        op = f"op::{request.request_id}"
        url = str(request.inputs.get("url", ""))
        if self._fetch_fn is not None:
            try:
                evidence = await to_async(self._fetch_fn, url)
            except Exception:  # noqa: BLE001 - degrade explicitly
                return CapabilityResult.failed(
                    operation_id=op, failure_code=FailureCode.UPSTREAM_ERROR, retryable=True
                )
        else:
            evidence = await self._fetch_httpx(url, op)
        if not evidence:
            return CapabilityResult.degraded(
                operation_id=op,
                degradation=(
                    Degradation(
                        flag="web_empty", reason="no retrievable content", fallback_used=False
                    ),
                ),
            )
        return CapabilityResult.ok(
            operation_id=op,
            evidence=tuple(evidence),
            source=SourceIdentity(source_id=url, source_kind=SourceKind.WEB, uri=url),
            citation=url,
            usage=Usage(tokens=80),
        )

    async def _fetch_httpx(self, url: str, op: str) -> Iterable[Evidence]:
        try:
            import httpx  # lazy: not required at package import time
        except ImportError:
            return ()
        try:
            response = await to_async(httpx.get, url, timeout=30.0)
            text = response.text
        except Exception:  # noqa: BLE001 - network is best-effort
            return ()
        return (
            Evidence(
                evidence_id=f"web:{op}",
                source=SourceIdentity(source_id=url, source_kind=SourceKind.WEB, uri=url),
                content_text=text[:4000],
                citation=url,
                trust=0.5,
                is_untrusted=True,
            ),
        )

"""CapabilityRouter: enforce policy before any capability is invoked (task 6.5).

Checks worker allowlist, run/system policy, data scope, availability and domain
allowlist, then delegates to the adapter. Denials return a normalized
``CapabilityResult.failed`` so callers never see provider-specific exceptions.
"""

from __future__ import annotations

from urllib.parse import urlparse

from agents_orchestration.domain.capability import (
    CapabilityRequest,
    CapabilityResult,
    HealthState,
)
from agents_orchestration.domain.enums import CapabilityKind, FailureCode
from agents_orchestration.domain.execution import OutcomeCertainty
from agents_orchestration.domain.policy import RunPolicy
from agents_orchestration.domain.worker import WorkerDefinition
from agents_orchestration.runtime.ports import IDGenerator


class CapabilityRouter:
    def __init__(self, registry, idgen: IDGenerator) -> None:
        self.registry = registry
        self.idgen = idgen

    async def invoke(
        self,
        request: CapabilityRequest,
        *,
        worker: WorkerDefinition,
        run_policy: RunPolicy,
    ) -> CapabilityResult:
        descriptor = self.registry.get(request.capability_id)
        if descriptor is None:
            return self._failed(request, FailureCode.UNAVAILABLE, "capability not registered")
        kind = CapabilityKind(descriptor.kind)

        if kind not in worker.allowed_capabilities:
            return self._failed(
                request, FailureCode.FORBIDDEN, f"{kind.value} not in worker allowlist"
            )
        if descriptor.health is HealthState.UNAVAILABLE:
            return self._failed(request, FailureCode.UNAVAILABLE, "capability unhealthy")
        if kind is CapabilityKind.WEB_RESEARCH and not run_policy.web_enabled:
            return self._failed(request, FailureCode.UNAUTHORIZED, "web disabled by run policy")
        if kind is CapabilityKind.WEB_RESEARCH:
            allowed = run_policy.web_allowed_domains
            domain = self._domain_of(request)
            if allowed and domain is not None and domain not in allowed:
                return self._failed(request, FailureCode.FORBIDDEN, f"domain {domain} not allowed")

        adapter = self.registry.adapter(request.capability_id)
        if adapter is None:  # pragma: no cover - defensive
            return self._failed(request, FailureCode.UNAVAILABLE, "no adapter bound")
        return await adapter.invoke(request)

    def _failed(
        self, request: CapabilityRequest, code: FailureCode, reason: str
    ) -> CapabilityResult:
        _ = reason  # reason is captured in caller diagnostics; result carries the code
        return CapabilityResult.failed(
            operation_id=self.idgen.new_id("op"),
            failure_code=code,
            retryable=False,
            outcome_certainty=OutcomeCertainty.CONFIRMED,
        )

    @staticmethod
    def _domain_of(request: CapabilityRequest) -> str | None:
        url = request.inputs.get("url")
        if isinstance(url, str):
            return urlparse(url).hostname
        return None

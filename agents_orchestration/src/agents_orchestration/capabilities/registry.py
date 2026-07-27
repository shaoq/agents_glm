"""Capability Adapter Port and Registry (tasks 6.4 / 6.6 / 12.5).

The Registry is the physical enforcement point for the read-only first release:
it refuses to register any WRITE-permission capability (task 12.5). Adapters
implement the async ``invoke`` Port and normalize provider results into
``CapabilityResult`` (task 6.6).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agents_orchestration.domain.capability import (
    CapabilityDescriptor,
    CapabilityPermission,
    CapabilityRequest,
    CapabilityResult,
    HealthState,
)
from agents_orchestration.domain.enums import CapabilityKind


class WriteCapabilityRejected(ValueError):
    """Raised when a WRITE capability is registered in the read-only release."""


@runtime_checkable
class CapabilityAdapter(Protocol):
    """Async capability Port implemented by Fake/Memory/RAG/Web/Model adapters."""

    descriptor: CapabilityDescriptor

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult: ...


class CapabilityRegistry:
    def __init__(self) -> None:
        self._descriptors: dict[str, CapabilityDescriptor] = {}
        self._adapters: dict[str, CapabilityAdapter] = {}

    def register(self, descriptor: CapabilityDescriptor, adapter: CapabilityAdapter) -> None:
        if descriptor.permission is CapabilityPermission.WRITE:
            raise WriteCapabilityRejected(
                f"first release is read-only: refusing WRITE capability {descriptor.capability_id}"
            )
        self._descriptors[descriptor.capability_id] = descriptor
        self._adapters[descriptor.capability_id] = adapter

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(capability_id)

    def adapter(self, capability_id: str) -> CapabilityAdapter | None:
        return self._adapters.get(capability_id)

    def find_kind(self, kind: CapabilityKind) -> CapabilityDescriptor | None:
        for descriptor in self._descriptors.values():
            if descriptor.kind == kind.value:
                return descriptor
        return None

    def allowed_kinds(self) -> frozenset[CapabilityKind]:
        return frozenset(
            CapabilityKind(d.kind)
            for d in self._descriptors.values()
            if d.kind in CapabilityKind._value2member_map_
        )

    def descriptors(self) -> list[CapabilityDescriptor]:
        return list(self._descriptors.values())

    def health_snapshot(self) -> dict[str, HealthState]:
        return {d.capability_id: d.health for d in self._descriptors.values()}

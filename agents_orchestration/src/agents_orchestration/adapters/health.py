"""Adapter health checks and safe diagnostics for ``capability doctor`` (task 7.9)."""

from __future__ import annotations

from agents_orchestration.capabilities.registry import CapabilityRegistry


def capability_doctor(registry: CapabilityRegistry) -> list[dict[str, object]]:
    """Return secret-safe diagnostics for every registered capability."""

    report: list[dict[str, object]] = []
    for descriptor in registry.descriptors():
        report.append(
            {
                "capability_id": descriptor.capability_id,
                "kind": descriptor.kind,
                "permission": descriptor.permission.value,
                "health": descriptor.health.value,
                "timeout_seconds": descriptor.timeout_seconds,
                "max_concurrency": descriptor.max_concurrency,
                "version": descriptor.version,
            }
        )
    return report

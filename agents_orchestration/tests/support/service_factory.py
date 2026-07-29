"""Deterministic test service factory.

Builds an :class:`OrchestrationService` wired with a real-adapter test registry
and a deterministic coordinator, so tests drive a full lifecycle with no live
provider and no network. This is the test-only replacement for the former
"offline service" default.
"""

from __future__ import annotations

from agents_orchestration.application.service import OrchestrationService
from tests.support.deterministic import build_deterministic_coordinator
from tests.support.test_registry import build_test_registry


def build_test_service(backend) -> OrchestrationService:
    """A deterministic service: real-adapter registry + deterministic coordinator."""

    return OrchestrationService(
        backend,
        capability_registry=build_test_registry(),
        coordinator=build_deterministic_coordinator(backend),
    )

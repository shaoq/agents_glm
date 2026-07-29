"""E2E fixtures: a deterministic clock and a fully-composed OrchestrationService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents_orchestration.adapters.base import descriptor_for
from agents_orchestration.adapters.memory import MemoryRecallAdapter
from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.domain.enums import CapabilityKind
from agents_orchestration.runtime.persistence.connection import SqliteBackend
from tests.support.deterministic import build_deterministic_coordinator
from tests.support.service_factory import build_test_service
from tests.support.test_registry import build_test_registry


class FakeClock:
    def __init__(self) -> None:
        self._t = datetime(2026, 7, 27, tzinfo=UTC)
        self._ticks = 0

    def now(self) -> datetime:
        self._ticks += 1
        return self._t + timedelta(seconds=self._ticks)

    def advance(self, seconds: int) -> None:
        self._ticks += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(tmp_path: Path, fake_clock: FakeClock) -> OrchestrationService:
    backend = SqliteBackend(tmp_path / "runtime.sqlite", tmp_path / "artifacts", clock=fake_clock)
    return build_test_service(backend)


@pytest.fixture
def empty_memory_service(tmp_path: Path, fake_clock: FakeClock) -> OrchestrationService:
    """A service whose Memory adapter returns no evidence (degradation E2E)."""

    backend = SqliteBackend(tmp_path / "runtime.sqlite", tmp_path / "artifacts", clock=fake_clock)
    registry = build_test_registry()
    # Replace the memory adapter with an empty-evidence one.
    empty = MemoryRecallAdapter(
        lambda query, scope: (),
        descriptor=descriptor_for(CapabilityKind.MEMORY_RECALL, "memory"),
    )
    registry.register(empty.descriptor, empty)
    return OrchestrationService(
        backend,
        capability_registry=registry,
        coordinator=build_deterministic_coordinator(backend),
    )

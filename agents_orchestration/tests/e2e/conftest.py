"""E2E fixtures: a deterministic clock and a fully-composed OrchestrationService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents_orchestration.adapters.fake import (
    FakeMemoryAdapter,
    build_fake_registry,
)
from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.runtime.persistence.connection import SqliteBackend


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
    return OrchestrationService(backend)


@pytest.fixture
def empty_memory_service(tmp_path: Path, fake_clock: FakeClock) -> OrchestrationService:
    """A service whose Memory adapter returns no evidence (degradation E2E)."""

    backend = SqliteBackend(tmp_path / "runtime.sqlite", tmp_path / "artifacts", clock=fake_clock)
    registry = build_fake_registry()
    # Replace the memory adapter with an empty-evidence one.
    empty = FakeMemoryAdapter(evidence=())
    memory_desc = empty.descriptor
    registry.register(memory_desc, empty)
    return OrchestrationService(backend, capability_registry=registry)

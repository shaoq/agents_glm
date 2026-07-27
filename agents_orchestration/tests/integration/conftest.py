"""Shared fixtures for integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents_orchestration.runtime.persistence.connection import SqliteBackend
from agents_orchestration.runtime.ports import Clock


class FakeClock:
    """Deterministic monotonic clock for tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._start = start or datetime(2026, 7, 27, tzinfo=UTC)
        self._ticks = 0

    def now(self) -> datetime:
        self._ticks += 1
        return self._start + timedelta(seconds=self._ticks)

    def advance(self, seconds: int) -> None:
        self._ticks += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def backend(tmp_path: Path, fake_clock: FakeClock) -> SqliteBackend:
    backend = SqliteBackend(
        sqlite_path=tmp_path / "runtime.sqlite",
        artifact_dir=tmp_path / "artifacts",
        clock=fake_clock,
    )
    yield backend
    backend.close()


def _clock_type() -> type:
    """Keeps the ``Clock`` Protocol import live for architecture assertions."""

    return Clock

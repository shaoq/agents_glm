"""SQLite connection backend (single-process Watch, short transactions).

The backend owns one connection (the runtime is single-process) and produces
:class:`SqliteUnitOfWork` instances. ``isolation_level=None`` puts the connection
in autocommit mode so the UnitOfWork can drive explicit ``BEGIN``/``COMMIT`` /
``ROLLBACK`` and keep capability calls outside the write transaction (design
Risks).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from agents_orchestration.runtime.persistence.schema import initialize
from agents_orchestration.runtime.ports import Clock, IDGenerator, SystemClock, UUIDIDGenerator

if TYPE_CHECKING:
    from agents_orchestration.runtime.persistence.unit_of_work import SqliteUnitOfWork


class SqliteBackend:
    """Owns the SQLite connection and produces atomic units of work."""

    def __init__(
        self,
        sqlite_path: str | Path,
        artifact_dir: str | Path,
        *,
        clock: Clock | None = None,
        idgen: IDGenerator | None = None,
    ) -> None:
        self.path = Path(sqlite_path)
        self.artifact_dir = Path(artifact_dir)
        self.clock: Clock = clock or SystemClock()
        self.idgen: IDGenerator = idgen or UUIDIDGenerator()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.path),
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        initialize(self.conn)

    def unit_of_work(self) -> SqliteUnitOfWork:
        from agents_orchestration.runtime.persistence.unit_of_work import SqliteUnitOfWork

        return SqliteUnitOfWork(self)

    def close(self) -> None:
        self.conn.close()

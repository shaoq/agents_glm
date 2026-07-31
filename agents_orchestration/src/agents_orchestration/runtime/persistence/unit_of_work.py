"""Atomic UnitOfWork spanning state, events, checkpoint and outbox (task 3.6).

All repositories share the UnitOfWork's connection and therefore one
``BEGIN IMMEDIATE`` .. ``COMMIT`` transaction. State transition, checkpoint,
event and outbox records are committed together; on rollback none of them are
visible (task 3.9). Capability calls are never executed inside this transaction.
"""

from __future__ import annotations

import sqlite3

from agents_orchestration.runtime.persistence.artifact_store import SqliteArtifactStore
from agents_orchestration.runtime.persistence.repositories import (
    SqliteAttemptRepository,
    SqliteCheckpointRepository,
    SqliteCompletionContractRepository,
    SqliteDependencyRepository,
    SqliteEventStore,
    SqliteEvidenceRepository,
    SqliteGateRepository,
    SqliteGoalRepository,
    SqliteLeaseRepository,
    SqliteOperationRepository,
    SqliteOutbox,
    SqlitePlanRepository,
    SqliteRequestDedupStore,
    SqliteResearchDirectionRepository,
    SqliteResearchLoopRepository,
    SqliteResearchStepRepository,
    SqliteRunRepository,
    SqliteStageExecutionRepository,
    SqliteTaskRepository,
)


class SqliteUnitOfWork:
    """One atomic transaction over all repositories."""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._conn: sqlite3.Connection = backend.conn
        self._active = False

        self.runs = SqliteRunRepository(self._conn)
        self.stages = SqliteStageExecutionRepository(self._conn)
        self.tasks = SqliteTaskRepository(self._conn)
        self.attempts = SqliteAttemptRepository(self._conn)
        self.plans = SqlitePlanRepository(self._conn)
        self.goals = SqliteGoalRepository(self._conn)
        self.completion = SqliteCompletionContractRepository(self._conn)
        self.dependencies = SqliteDependencyRepository(self._conn)
        self.leases = SqliteLeaseRepository(self._conn)
        self.gates = SqliteGateRepository(self._conn)
        self.checkpoints = SqliteCheckpointRepository(self._conn)
        self.operations = SqliteOperationRepository(self._conn)
        self.research_loops = SqliteResearchLoopRepository(self._conn)
        self.research_directions = SqliteResearchDirectionRepository(self._conn)
        self.research_steps = SqliteResearchStepRepository(self._conn)
        self.events = SqliteEventStore(self._conn)
        self.outbox = SqliteOutbox(self._conn)
        self.artifacts = SqliteArtifactStore(self._conn, backend.artifact_dir)
        self.dedup = SqliteRequestDedupStore(self._conn, backend.clock)
        self.evidence = SqliteEvidenceRepository(self._conn)

    def __enter__(self) -> SqliteUnitOfWork:
        if self._active:
            raise RuntimeError("UnitOfWork is already active")
        self._conn.execute("BEGIN IMMEDIATE")
        self._active = True
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self._active:
            # Uncommitted work is rolled back whether or not an exception occurred;
            # callers must call commit() to persist.
            self._conn.execute("ROLLBACK")
            self._active = False
        return False

    def commit(self) -> None:
        if not self._active:
            raise RuntimeError("UnitOfWork is not active")
        self._conn.execute("COMMIT")
        self._active = False

    def rollback(self) -> None:
        if self._active:
            self._conn.execute("ROLLBACK")
            self._active = False

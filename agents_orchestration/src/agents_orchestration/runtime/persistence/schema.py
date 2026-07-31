"""SQLite schema creation and version tracking (design Decision 4 / task 3.2).

Each table stores the query key columns (primary key, run/task linkage, state,
versions) plus a ``data`` JSON blob holding the full immutable domain object.
This keeps compare-and-set and indexed lookups cheap while mappers stay trivial
and the schema stays stable as models evolve.

Schema version is tracked via ``pragma user_version``.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    state          TEXT NOT NULL,
    state_version  INTEGER NOT NULL,
    termination    TEXT,
    data           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_state ON runs (state);

CREATE TABLE IF NOT EXISTS goal_versions (
    run_id  TEXT PRIMARY KEY,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS completion_contracts (
    run_id  TEXT PRIMARY KEY,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_versions (
    run_id     TEXT NOT NULL,
    version    INTEGER NOT NULL,
    acceptance TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (run_id, version)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    plan_version  INTEGER NOT NULL,
    state         TEXT NOT NULL,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tasks_run_plan ON tasks (run_id, plan_version);
CREATE INDEX IF NOT EXISTS ix_tasks_run_state ON tasks (run_id, state);

CREATE TABLE IF NOT EXISTS task_dependencies (
    run_id       TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    predecessor  TEXT NOT NULL,
    successor    TEXT NOT NULL,
    data         TEXT NOT NULL,
    PRIMARY KEY (run_id, plan_version, predecessor, successor)
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    state      TEXT NOT NULL,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts_task ON attempts (task_id);
CREATE INDEX IF NOT EXISTS ix_attempts_run ON attempts (run_id);

CREATE TABLE IF NOT EXISTS leases (
    task_id     TEXT NOT NULL,
    attempt_id  TEXT NOT NULL,
    epoch       INTEGER NOT NULL,
    state       TEXT NOT NULL,
    data        TEXT NOT NULL,
    PRIMARY KEY (task_id, epoch)
);
CREATE INDEX IF NOT EXISTS ix_leases_state ON leases (state);

CREATE TABLE IF NOT EXISTS operations (
    operation_id     TEXT PRIMARY KEY,
    attempt_id       TEXT NOT NULL,
    dedup_request_id TEXT NOT NULL,
    data             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_operations_attempt ON operations (attempt_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_operations_dedup_request
    ON operations (dedup_request_id);

CREATE TABLE IF NOT EXISTS gates (
    gate_id TEXT PRIMARY KEY,
    run_id  TEXT NOT NULL,
    state   TEXT NOT NULL,
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_gates_run_state ON gates (run_id, state);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_checkpoints_run ON checkpoints (run_id);

CREATE TABLE IF NOT EXISTS events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    run_id        TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_run_version ON events (run_id, state_version);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    event_id     TEXT NOT NULL,
    published_at TEXT,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox (published_at);

CREATE TABLE IF NOT EXISTS artifact_metadata (
    artifact_id  TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    path         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_artifact_hash ON artifact_metadata (content_hash);

CREATE TABLE IF NOT EXISTS request_deduplication (
    request_id TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    result     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_executions (
    stage_execution_id TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL,
    phase              TEXT NOT NULL,
    logical_stage_key  TEXT NOT NULL,
    fingerprint_hex    TEXT NOT NULL,
    status             TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL,
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    failure_code       TEXT,
    data               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stage_exec_run_key
    ON stage_executions (run_id, logical_stage_key);
CREATE INDEX IF NOT EXISTS ix_stage_exec_idempotency
    ON stage_executions (idempotency_key);
-- At most one ACCEPTED result per Run + logical stage + input fingerprint
-- (design Decision 5 / task 3.5). Enforced at the storage boundary so concurrent
-- accepts cannot produce duplicate accepted results.
CREATE UNIQUE INDEX IF NOT EXISTS ux_stage_exec_accepted
    ON stage_executions (run_id, logical_stage_key, fingerprint_hex)
    WHERE status = 'accepted';

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    attempt_id   TEXT,
    source_kind  TEXT NOT NULL,
    data         TEXT NOT NULL,
    PRIMARY KEY (run_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS ix_evidence_run ON evidence (run_id);

CREATE TABLE IF NOT EXISTS research_loops (
    loop_id        TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    plan_version   INTEGER NOT NULL,
    task_id        TEXT NOT NULL,
    status         TEXT NOT NULL,
    state_version  INTEGER NOT NULL,
    data           TEXT NOT NULL,
    UNIQUE (run_id, plan_version, task_id)
);
CREATE INDEX IF NOT EXISTS ix_research_loops_run
    ON research_loops (run_id, plan_version, status);

CREATE TABLE IF NOT EXISTS research_directions (
    direction_id TEXT PRIMARY KEY,
    loop_id      TEXT NOT NULL,
    focus_hash   TEXT NOT NULL,
    data         TEXT NOT NULL,
    UNIQUE (loop_id, focus_hash)
);
CREATE INDEX IF NOT EXISTS ix_research_directions_loop
    ON research_directions (loop_id);

CREATE TABLE IF NOT EXISTS research_steps (
    step_id               TEXT PRIMARY KEY,
    loop_id               TEXT NOT NULL,
    run_id                TEXT NOT NULL,
    plan_version          INTEGER NOT NULL,
    task_id               TEXT NOT NULL,
    step_index            INTEGER NOT NULL,
    status                TEXT NOT NULL,
    decision_request_id   TEXT NOT NULL UNIQUE,
    capability_request_id TEXT NOT NULL UNIQUE,
    data                  TEXT NOT NULL,
    UNIQUE (run_id, plan_version, task_id, step_index)
);
CREATE INDEX IF NOT EXISTS ix_research_steps_loop
    ON research_steps (loop_id, step_index);
CREATE INDEX IF NOT EXISTS ix_research_steps_active
    ON research_steps (run_id, plan_version, task_id, status);
"""


def initialize(conn: sqlite3.Connection) -> None:
    """Create all tables (idempotent) and stamp the schema version."""

    conn.executescript(_SCHEMA)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])

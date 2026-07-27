"""SQLite repository implementations for every aggregate (tasks 3.3 / 3.4).

Compare-and-set on ``runs.state_version`` (task 3.5) lives in
:class:`SqliteRunRepository`; lease epoch fencing lives in
:class:`SqliteLeaseRepository`. All repositories share the UnitOfWork's
connection so writes participate in one atomic transaction (task 3.6).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence

from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Attempt, Operation, Run, Task
from agents_orchestration.domain.goal import CompletionContract, GoalSpec
from agents_orchestration.domain.lifecycle import Checkpoint, Gate, Lease
from agents_orchestration.domain.plan import Dependency, Plan
from agents_orchestration.runtime.persistence.mappers import dump, load
from agents_orchestration.runtime.ports import ConcurrencyError, StaleVersionError

_TERMINAL_RUN_STATES = ("succeeded", "failed", "canceled", "paused")


def _term(value: object | None) -> str | None:
    return value.value if value is not None else None  # type: ignore[attr-defined]


class SqliteRunRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, run_id: str) -> Run | None:
        row = self.conn.execute("SELECT data FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return load(Run, row["data"]) if row else None

    def save(self, run: Run, expected_version: int) -> None:
        cur = self.conn.execute(
            "UPDATE runs SET state = ?, state_version = ?, termination = ?, data = ? "
            "WHERE run_id = ? AND state_version = ?",
            (
                run.state.value,
                run.state_version,
                _term(run.termination),
                dump(run),
                run.run_id,
                expected_version,
            ),
        )
        if cur.rowcount == 0:
            if self.get(run.run_id) is None:
                self.conn.execute(
                    "INSERT INTO runs (run_id, state, state_version, termination, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.state.value,
                        run.state_version,
                        _term(run.termination),
                        dump(run),
                    ),
                )
            else:
                raise StaleVersionError(
                    f"Run {run.run_id} CAS failed: expected state_version {expected_version}"
                )

    def list_resumable(self) -> list[Run]:
        rows = self.conn.execute(
            "SELECT data FROM runs WHERE state NOT IN (?, ?, ?, ?)", _TERMINAL_RUN_STATES
        )
        return [load(Run, r["data"]) for r in rows]


class SqliteTaskRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, task_id: str) -> Task | None:
        row = self.conn.execute("SELECT data FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return load(Task, row["data"]) if row else None

    def save(self, task: Task) -> None:
        self.conn.execute(
            "INSERT INTO tasks (task_id, run_id, plan_version, state, data) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "run_id = excluded.run_id, plan_version = excluded.plan_version, "
            "state = excluded.state, data = excluded.data",
            (task.task_id, task.run_id, task.plan_version, task.state.value, dump(task)),
        )

    def materialize(self, tasks: Sequence[Task]) -> None:
        for task in tasks:
            self.save(task)

    def by_run(self, run_id: str, plan_version: int | None = None) -> list[Task]:
        if plan_version is None:
            rows = self.conn.execute(
                "SELECT data FROM tasks WHERE run_id = ? ORDER BY plan_version", (run_id,)
            )
        else:
            rows = self.conn.execute(
                "SELECT data FROM tasks WHERE run_id = ? AND plan_version = ? "
                "ORDER BY plan_version",
                (run_id, plan_version),
            )
        return [load(Task, r["data"]) for r in rows]


class SqliteAttemptRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, attempt_id: str) -> Attempt | None:
        row = self.conn.execute(
            "SELECT data FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return load(Attempt, row["data"]) if row else None

    def save(self, attempt: Attempt) -> None:
        self.conn.execute(
            "INSERT INTO attempts (attempt_id, task_id, run_id, state, data) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(attempt_id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (
                attempt.attempt_id,
                attempt.task_id,
                attempt.run_id,
                attempt.state.value,
                dump(attempt),
            ),
        )

    def active_for_task(self, task_id: str) -> Attempt | None:
        row = self.conn.execute(
            "SELECT data FROM attempts WHERE task_id = ? AND state = 'dispatched' "
            "ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return load(Attempt, row["data"]) if row else None

    def by_run(self, run_id: str) -> list[Attempt]:
        rows = self.conn.execute("SELECT data FROM attempts WHERE run_id = ?", (run_id,))
        return [load(Attempt, r["data"]) for r in rows]


class SqlitePlanRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, run_id: str, version: int) -> Plan | None:
        row = self.conn.execute(
            "SELECT data FROM plan_versions WHERE run_id = ? AND version = ?", (run_id, version)
        ).fetchone()
        return load(Plan, row["data"]) if row else None

    def current(self, run_id: str) -> Plan | None:
        row = self.conn.execute(
            "SELECT data FROM plan_versions WHERE run_id = ? ORDER BY version DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return load(Plan, row["data"]) if row else None

    def save(self, plan: Plan) -> None:
        self.conn.execute(
            "INSERT INTO plan_versions (run_id, version, acceptance, data) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(run_id, version) DO UPDATE SET "
            "acceptance = excluded.acceptance, data = excluded.data",
            (plan.run_id, plan.version, plan.acceptance.value, dump(plan)),
        )


class SqliteGoalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, run_id: str, goal: GoalSpec) -> None:
        self.conn.execute(
            "INSERT INTO goal_versions (run_id, data) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET data = excluded.data",
            (run_id, dump(goal)),
        )

    def get(self, run_id: str) -> GoalSpec | None:
        row = self.conn.execute(
            "SELECT data FROM goal_versions WHERE run_id = ?", (run_id,)
        ).fetchone()
        return load(GoalSpec, row["data"]) if row else None


class SqliteCompletionContractRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, run_id: str, contract: CompletionContract) -> None:
        self.conn.execute(
            "INSERT INTO completion_contracts (run_id, data) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET data = excluded.data",
            (run_id, dump(contract)),
        )

    def get(self, run_id: str) -> CompletionContract | None:
        row = self.conn.execute(
            "SELECT data FROM completion_contracts WHERE run_id = ?", (run_id,)
        ).fetchone()
        return load(CompletionContract, row["data"]) if row else None


class SqliteDependencyRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, run_id: str, plan_version: int, deps: Sequence[Dependency]) -> None:
        for dep in deps:
            self.conn.execute(
                "INSERT INTO task_dependencies "
                "(run_id, plan_version, predecessor, successor, data) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, plan_version, predecessor, successor) "
                "DO UPDATE SET data = excluded.data",
                (run_id, plan_version, dep.predecessor, dep.successor, dump(dep)),
            )

    def by_plan(self, run_id: str, plan_version: int) -> list[Dependency]:
        rows = self.conn.execute(
            "SELECT data FROM task_dependencies WHERE run_id = ? AND plan_version = ?",
            (run_id, plan_version),
        )
        return [load(Dependency, r["data"]) for r in rows]


class SqliteLeaseRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, task_id: str) -> Lease | None:
        row = self.conn.execute(
            "SELECT data FROM leases WHERE task_id = ? ORDER BY epoch DESC LIMIT 1", (task_id,)
        ).fetchone()
        return load(Lease, row["data"]) if row else None

    def save(self, lease: Lease, expected_epoch: int | None = None) -> None:
        if expected_epoch is None:
            self.conn.execute(
                "INSERT INTO leases (task_id, attempt_id, epoch, state, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (lease.task_id, lease.attempt_id, lease.epoch, lease.state.value, dump(lease)),
            )
            return
        # Fencing: reject if a newer epoch already exists for this task (the lease
        # was reclaimed/recovered by a newer attempt while this holder stalled).
        row = self.conn.execute(
            "SELECT COALESCE(MAX(epoch), 0) AS m FROM leases WHERE task_id = ?",
            (lease.task_id,),
        ).fetchone()
        if expected_epoch < int(row["m"]):
            raise ConcurrencyError(
                f"Lease fencing: task {lease.task_id} epoch {expected_epoch} "
                f"superseded by epoch {row['m']}"
            )
        cur = self.conn.execute(
            "UPDATE leases SET state = ?, data = ? WHERE task_id = ? AND epoch = ?",
            (lease.state.value, dump(lease), lease.task_id, expected_epoch),
        )
        if cur.rowcount == 0:
            raise ConcurrencyError(
                f"Lease fencing: task {lease.task_id} epoch {expected_epoch} not found"
            )

    def active(self) -> list[Lease]:
        rows = self.conn.execute("SELECT data FROM leases WHERE state IN ('claimed', 'renewed')")
        return [load(Lease, r["data"]) for r in rows]


class SqliteGateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, gate_id: str) -> Gate | None:
        row = self.conn.execute("SELECT data FROM gates WHERE gate_id = ?", (gate_id,)).fetchone()
        return load(Gate, row["data"]) if row else None

    def save(self, gate: Gate) -> None:
        self.conn.execute(
            "INSERT INTO gates (gate_id, run_id, state, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(gate_id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (gate.gate_id, gate.run_id, gate.state.value, dump(gate)),
        )

    def open_for_run(self, run_id: str) -> list[Gate]:
        rows = self.conn.execute(
            "SELECT data FROM gates WHERE run_id = ? AND state = 'open'", (run_id,)
        )
        return [load(Gate, r["data"]) for r in rows]


class SqliteCheckpointRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, checkpoint: Checkpoint) -> None:
        self.conn.execute(
            "INSERT INTO checkpoints (checkpoint_id, run_id, data) VALUES (?, ?, ?) "
            "ON CONFLICT(checkpoint_id) DO UPDATE SET data = excluded.data",
            (checkpoint.checkpoint_id, checkpoint.run_id, dump(checkpoint)),
        )

    def latest(self, run_id: str) -> Checkpoint | None:
        row = self.conn.execute(
            "SELECT data FROM checkpoints WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return load(Checkpoint, row["data"]) if row else None


class SqliteOperationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, operation: Operation) -> None:
        self.conn.execute(
            "INSERT INTO operations (operation_id, attempt_id, dedup_request_id, data) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(operation_id) DO UPDATE SET data = excluded.data",
            (
                operation.operation_id,
                operation.attempt_id,
                operation.dedup_request_id,
                dump(operation),
            ),
        )

    def get(self, operation_id: str) -> Operation | None:
        row = self.conn.execute(
            "SELECT data FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        return load(Operation, row["data"]) if row else None

    def by_attempt(self, attempt_id: str) -> list[Operation]:
        rows = self.conn.execute("SELECT data FROM operations WHERE attempt_id = ?", (attempt_id,))
        return [load(Operation, r["data"]) for r in rows]


class SqliteEventStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(self, events: Sequence[DomainEvent]) -> None:
        for event in events:
            self.conn.execute(
                "INSERT INTO events (event_id, run_id, state_version, data) VALUES (?, ?, ?, ?)",
                (event.event_id, event.run_id, event.state_version, dump(event)),
            )

    def stream(
        self, run_id: str, *, after_state_version: int | None = None
    ) -> Iterator[DomainEvent]:
        if after_state_version is None:
            rows = self.conn.execute(
                "SELECT data FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
            )
        else:
            rows = self.conn.execute(
                "SELECT data FROM events WHERE run_id = ? AND state_version > ? ORDER BY seq",
                (run_id, after_state_version),
            )
        for row in rows:
            yield load(DomainEvent, row["data"])


class SqliteOutbox:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def enqueue(self, run_id: str, events: Sequence[DomainEvent]) -> None:
        for event in events:
            self.conn.execute(
                "INSERT INTO outbox (run_id, event_id, data) VALUES (?, ?, ?)",
                (run_id, event.event_id, dump(event)),
            )

    def pending(self) -> list[tuple[int, DomainEvent]]:
        rows = self.conn.execute(
            "SELECT outbox_id, data FROM outbox WHERE published_at IS NULL ORDER BY outbox_id"
        )
        return [(row["outbox_id"], load(DomainEvent, row["data"])) for row in rows]

    def mark_published(self, outbox_id: int) -> None:
        self.conn.execute("UPDATE outbox SET published_at = '1' WHERE outbox_id = ?", (outbox_id,))


class SqliteRequestDedupStore:
    def __init__(self, conn: sqlite3.Connection, clock) -> None:
        self.conn = conn
        self.clock = clock

    def try_claim(self, request_id: str, *, run_id: str, kind: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO request_deduplication (request_id, run_id, kind, created_at) "
                "VALUES (?, ?, ?, ?)",
                (request_id, run_id, kind, self.clock.now().isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def remember(self, request_id: str, result: object) -> None:
        self.conn.execute(
            "UPDATE request_deduplication SET result = ? WHERE request_id = ?",
            (json.dumps(result, default=str), request_id),
        )

    def recall(self, request_id: str) -> object | None:
        row = self.conn.execute(
            "SELECT result FROM request_deduplication WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None or row["result"] is None:
            return None
        return json.loads(row["result"])

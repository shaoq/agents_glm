import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agents_memory.models import (
    EventFrame,
    IndexOperation,
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    MemoryType,
    PendingResolution,
    PendingResolutionStatus,
    RelationKind,
    SourceKind,
    Validity,
    WriteReport,
)


class IdempotencyConflict(ValueError):
    pass


class StaleMemoryState(RuntimeError):
    pass


class StoredRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    input_hash: str
    status: str
    report: WriteReport | None = None


class MemoryRepository:
    """SQLite truth store for memory, provenance, lifecycle, and repair logs.

    Methods accept an optional connection so StorageCoordinator can compose
    several operations into one transaction. When omitted, a method owns its
    short connection and commit.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_version(version)
                SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT,
                    session_id TEXT,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    validity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT,
                    event_frame_json TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories(user_id, agent_id, session_id, type, validity);

                CREATE TABLE IF NOT EXISTS memory_sources (
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS memory_relations (
                    from_memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    to_memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(from_memory_id, to_memory_id, relation)
                );

                CREATE TABLE IF NOT EXISTS write_requests (
                    request_id TEXT PRIMARY KEY,
                    input_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS index_operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_index_operations_status
                ON index_operations(status, updated_at);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "event_frame_json" not in columns:
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN event_frame_json TEXT"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_resolutions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT,
                    session_id TEXT,
                    status TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pending_scope_status
                ON pending_resolutions(
                    user_id, agent_id, session_id, status, expires_at
                );
                UPDATE schema_version SET version = 2 WHERE version < 2;
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Own one all-or-nothing boundary for a complete write plan."""

        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # Memory truth and provenance

    def save_memory(
        self,
        record: MemoryRecord,
        sources: tuple[MemorySource, ...] = (),
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owned = connection is None
        conn = connection or self._connect()
        try:
            conn.execute(
                """
                INSERT INTO memories (
                    id, user_id, agent_id, session_id, type, content, importance,
                    confidence, validity, created_at, updated_at, valid_from,
                    event_frame_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.scope.user_id,
                    record.scope.agent_id,
                    record.scope.session_id,
                    record.type.value,
                    record.content,
                    record.importance,
                    record.confidence,
                    record.validity.value,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.valid_from.isoformat() if record.valid_from else None,
                    record.event_frame.model_dump_json()
                    if record.event_frame
                    else None,
                    json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
            for source in sources:
                conn.execute(
                    """
                    INSERT INTO memory_sources (
                        memory_id, message_id, role, source_kind, excerpt, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source.memory_id,
                        source.message_id,
                        source.role,
                        source.source_kind.value,
                        source.excerpt,
                        source.created_at.isoformat(),
                    ),
                )
            if owned:
                conn.commit()
        finally:
            if owned:
                conn.close()

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return self._record_from_row(row) if row else None

    def get_memories(self, memory_ids: list[str]) -> list[MemoryRecord]:
        return [record for memory_id in memory_ids if (record := self.get_memory(memory_id))]

    def list_memories(
        self,
        scope: MemoryScope,
        type: MemoryType | None = None,
        *,
        include_history: bool = False,
    ) -> list[MemoryRecord]:
        clauses = [
            "user_id = ?",
            "agent_id IS ?",
            "session_id IS ?",
        ]
        params: list[object] = [scope.user_id, scope.agent_id, scope.session_id]
        if type is not None:
            clauses.append("type = ?")
            params.append(type.value)
        if not include_history:
            clauses.append("validity = ?")
            params.append(Validity.ACTIVE.value)
        query = f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY created_at"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_all_active_memories(self) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE validity = ? ORDER BY created_at",
                (Validity.ACTIVE.value,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_unsynced_active_memories(
        self, scope: MemoryScope, type: MemoryType
    ) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT m.* FROM memories m
                JOIN index_operations op ON op.memory_id = m.id
                WHERE m.user_id = ? AND m.agent_id IS ? AND m.session_id IS ?
                  AND m.type = ? AND m.validity = ?
                  AND op.kind = ? AND op.status IN (?, ?)
                ORDER BY m.created_at
                """,
                (
                    scope.user_id,
                    scope.agent_id,
                    scope.session_id,
                    type.value,
                    Validity.ACTIVE.value,
                    IndexOperationKind.UPSERT.value,
                    IndexOperationStatus.PENDING.value,
                    IndexOperationStatus.FAILED.value,
                ),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def get_sources(self, memory_id: str) -> list[MemorySource]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_sources WHERE memory_id = ? ORDER BY created_at",
                (memory_id,),
            ).fetchall()
        return [
            MemorySource(
                memory_id=row["memory_id"],
                message_id=row["message_id"],
                role=row["role"],
                source_kind=SourceKind(row["source_kind"]),
                excerpt=row["excerpt"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def transition(
        self,
        memory_id: str,
        validity: Validity,
        relation: MemoryRelation,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Move exactly one active version to history and link its successor.

        The active predicate is a last-moment stale-state guard: plans are
        computed before the transaction, so another completed write must not
        be overwritten silently.
        """

        owned = connection is None
        conn = connection or self._connect()
        try:
            result = conn.execute(
                """
                UPDATE memories SET validity = ?, updated_at = ?
                WHERE id = ? AND validity = ?
                """,
                (
                    validity.value,
                    datetime.now(UTC).isoformat(),
                    memory_id,
                    Validity.ACTIVE.value,
                ),
            )
            if result.rowcount != 1:
                raise StaleMemoryState(
                    f"memory {memory_id} is no longer active"
                )
            conn.execute(
                """
                INSERT INTO memory_relations (
                    from_memory_id, to_memory_id, relation, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    relation.from_memory_id,
                    relation.to_memory_id,
                    relation.relation.value,
                    relation.created_at.isoformat(),
                ),
            )
            if owned:
                conn.commit()
        finally:
            if owned:
                conn.close()

    def get_relations(self, memory_id: str) -> list[MemoryRelation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_relations
                WHERE from_memory_id = ? OR to_memory_id = ?
                ORDER BY created_at
                """,
                (memory_id, memory_id),
            ).fetchall()
        return [
            MemoryRelation(
                from_memory_id=row["from_memory_id"],
                to_memory_id=row["to_memory_id"],
                relation=RelationKind(row["relation"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # Request idempotency

    def save_request(
        self,
        request_id: str,
        input_hash: str,
        status: str,
        report: WriteReport | None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owned = connection is None
        conn = connection or self._connect()
        now = datetime.now(UTC).isoformat()
        try:
            conn.execute(
                """
                INSERT INTO write_requests (
                    request_id, input_hash, status, report_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status = excluded.status,
                    report_json = excluded.report_json,
                    updated_at = excluded.updated_at
                WHERE write_requests.input_hash = excluded.input_hash
                """,
                (
                    request_id,
                    input_hash,
                    status,
                    report.model_dump_json() if report else None,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT input_hash FROM write_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row["input_hash"] != input_hash:
                raise IdempotencyConflict(request_id)
            if owned:
                conn.commit()
        finally:
            if owned:
                conn.close()

    def reserve_request(
        self,
        request_id: str,
        input_hash: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        owned = connection is None
        conn = connection or self._connect()
        now = datetime.now(UTC).isoformat()
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO write_requests (
                        request_id, input_hash, status, report_json, created_at, updated_at
                    ) VALUES (?, ?, 'processing', NULL, ?, ?)
                    """,
                    (request_id, input_hash, now, now),
                )
                if owned:
                    conn.commit()
                return True
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT input_hash FROM write_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is not None and row["input_hash"] != input_hash:
                    raise IdempotencyConflict(request_id)
                return False
        finally:
            if owned:
                conn.close()

    def get_request(self, request_id: str, input_hash: str | None = None) -> StoredRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM write_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        if input_hash is not None and row["input_hash"] != input_hash:
            raise IdempotencyConflict(request_id)
        return StoredRequest(
            request_id=row["request_id"],
            input_hash=row["input_hash"],
            status=row["status"],
            report=WriteReport.model_validate_json(row["report_json"])
            if row["report_json"]
            else None,
        )

    # Derivative-index outbox

    def enqueue_index_operation(
        self,
        request_id: str,
        memory_id: str,
        kind: IndexOperationKind,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        owned = connection is None
        conn = connection or self._connect()
        now = datetime.now(UTC).isoformat()
        try:
            cursor = conn.execute(
                """
                INSERT INTO index_operations (
                    request_id, memory_id, kind, status, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    request_id,
                    memory_id,
                    kind.value,
                    IndexOperationStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            if owned:
                conn.commit()
            return int(cursor.lastrowid)
        finally:
            if owned:
                conn.close()

    def list_index_operations(
        self, status: IndexOperationStatus | None = None
    ) -> list[IndexOperation]:
        query = "SELECT * FROM index_operations"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            IndexOperation(
                id=row["id"],
                request_id=row["request_id"],
                memory_id=row["memory_id"],
                kind=IndexOperationKind(row["kind"]),
                status=IndexOperationStatus(row["status"]),
                attempts=row["attempts"],
                last_error=row["last_error"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def mark_index_operation(
        self,
        operation_id: int,
        status: IndexOperationStatus,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE index_operations
                SET status = ?, attempts = attempts + 1, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error, datetime.now(UTC).isoformat(), operation_id),
            )

    def delete_memory(
        self, memory_id: str, user_id: str, *, connection: sqlite3.Connection | None = None
    ) -> bool:
        owned = connection is None
        conn = connection or self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)
            )
            if owned:
                conn.commit()
            return cursor.rowcount == 1
        finally:
            if owned:
                conn.close()

    # Deferred event assertions

    def save_pending_resolution(
        self,
        pending: PendingResolution,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owned = connection is None
        conn = connection or self._connect()
        try:
            existing_row = conn.execute(
                """
                SELECT status, user_id, agent_id, session_id
                FROM pending_resolutions WHERE id = ?
                """,
                (pending.id,),
            ).fetchone()
            if existing_row is not None:
                existing_scope = (
                    existing_row["user_id"],
                    existing_row["agent_id"],
                    existing_row["session_id"],
                )
                if existing_scope != pending.scope.as_key():
                    raise ValueError("pending resolution scope is immutable")
                existing_status = PendingResolutionStatus(
                    existing_row["status"]
                )
                if (
                    existing_status is not PendingResolutionStatus.OPEN
                    and pending.status is not existing_status
                ):
                    raise ValueError("terminal pending resolution cannot transition")
            conn.execute(
                """
                INSERT INTO pending_resolutions (
                    id, user_id, agent_id, session_id, status, importance,
                    expires_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    importance = excluded.importance,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    pending.id,
                    pending.scope.user_id,
                    pending.scope.agent_id,
                    pending.scope.session_id,
                    pending.status.value,
                    pending.importance,
                    pending.expires_at.isoformat(),
                    pending.updated_at.isoformat(),
                    pending.model_dump_json(),
                ),
            )
            if owned:
                conn.commit()
        finally:
            if owned:
                conn.close()

    def get_pending_resolution(
        self, resolution_id: str
    ) -> PendingResolution | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pending_resolutions WHERE id = ?",
                (resolution_id,),
            ).fetchone()
        return (
            PendingResolution.model_validate_json(row["payload_json"]) if row else None
        )

    def list_pending_resolutions(
        self,
        scope: MemoryScope | None = None,
        status: PendingResolutionStatus | None = PendingResolutionStatus.OPEN,
    ) -> list[PendingResolution]:
        clauses: list[str] = []
        params: list[object] = []
        if scope is not None:
            clauses.extend(
                ["user_id = ?", "agent_id IS ?", "session_id IS ?"]
            )
            params.extend([scope.user_id, scope.agent_id, scope.session_id])
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        query = "SELECT payload_json FROM pending_resolutions"
        if clauses:
            query += f" WHERE {' AND '.join(clauses)}"
        query += " ORDER BY updated_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            PendingResolution.model_validate_json(row["payload_json"]) for row in rows
        ]

    def sweep_pending_resolutions(
        self, *, now: datetime | None = None
    ) -> int:
        """Expire or obsolete pending rows without making semantic guesses.

        Maintenance never calls a relation model. It only applies lifecycle
        facts available in SQLite: TTL expiry or absence of every active
        conflicting target.
        """

        current = now or datetime.now(UTC)
        changed = 0
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM pending_resolutions
                WHERE status = ?
                ORDER BY expires_at, id
                """,
                (PendingResolutionStatus.OPEN.value,),
            ).fetchall()
            for row in rows:
                pending = PendingResolution.model_validate_json(row["payload_json"])
                status: PendingResolutionStatus | None = None
                if pending.expires_at <= current:
                    status = PendingResolutionStatus.EXPIRED
                elif pending.conflicting_memory_ids:
                    placeholders = ",".join(
                        "?" for _ in pending.conflicting_memory_ids
                    )
                    active = connection.execute(
                        f"""
                        SELECT COUNT(*) FROM memories
                        WHERE id IN ({placeholders}) AND validity = ?
                        """,
                        (
                            *pending.conflicting_memory_ids,
                            Validity.ACTIVE.value,
                        ),
                    ).fetchone()[0]
                    if active == 0:
                        status = PendingResolutionStatus.OBSOLETE
                if status is None:
                    continue
                updated = pending.model_copy(
                    update={
                        "status": status,
                        "updated_at": current,
                        "last_evaluated_at": current,
                    }
                )
                self.save_pending_resolution(updated, connection=connection)
                changed += 1
        return changed

    def cleanup_pending_resolutions(self, *, before: datetime) -> int:
        """Delete only terminal pending rows older than the retention cutoff."""

        terminal = (
            PendingResolutionStatus.RESOLVED.value,
            PendingResolutionStatus.EXPIRED.value,
            PendingResolutionStatus.OBSOLETE.value,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM pending_resolutions
                WHERE status IN (?, ?, ?) AND updated_at < ?
                """,
                (*terminal, before.isoformat()),
            )
        return cursor.rowcount

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            scope=MemoryScope(
                user_id=row["user_id"],
                agent_id=row["agent_id"],
                session_id=row["session_id"],
            ),
            type=MemoryType(row["type"]),
            content=row["content"],
            importance=row["importance"],
            confidence=row["confidence"],
            validity=Validity(row["validity"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            valid_from=datetime.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
            event_frame=(
                EventFrame.model_validate_json(row["event_frame_json"])
                if row["event_frame_json"]
                else None
            ),
            metadata=json.loads(row["metadata_json"]),
        )

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agents_memory.models import (
    IndexOperation,
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemorySource,
    MemoryType,
    RelationKind,
    SourceKind,
    Validity,
    WriteReport,
)


class IdempotencyConflict(ValueError):
    pass


class StoredRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    input_hash: str
    status: str
    report: WriteReport | None = None


class MemoryRepository:
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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
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
                    confidence, validity, created_at, updated_at, valid_from, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        owned = connection is None
        conn = connection or self._connect()
        try:
            result = conn.execute(
                "UPDATE memories SET validity = ?, updated_at = ? WHERE id = ?",
                (validity.value, datetime.now(UTC).isoformat(), memory_id),
            )
            if result.rowcount != 1:
                raise KeyError(memory_id)
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
            metadata=json.loads(row["metadata_json"]),
        )

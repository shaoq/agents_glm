"""Read-only, user-bound, bounded Recall queries over the SQLite truth store.

All methods enforce ``user_id`` and accept a hard ``limit``. None mutate
state or touch the index sync outbox. The schema is owned by
``MemoryRepository``; these queries assume it already exists (Recall runs
after writes have created the tables).

Reference: design 13.4 (additive Repository read capabilities).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agents_memory.models import (
    EventFrame,
    IndexOperationKind,
    IndexOperationStatus,
    MemoryRecord,
    MemoryRelation,
    MemoryScope,
    MemoryType,
    RelationKind,
    Validity,
)


class RecallReadRepository:
    """Read-only Recall storage surface over the SQLite truth store."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def load_memories_by_ids(
        self,
        memory_ids: tuple[str, ...],
        *,
        user_id: str,
        limit: int,
    ) -> list[MemoryRecord]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        query = (
            f"SELECT * FROM memories WHERE user_id = ? AND id IN ({placeholders}) "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        params: list[object] = [user_id, *memory_ids, limit]
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def query_scoped_memories(
        self,
        scope: MemoryScope,
        *,
        types: tuple[MemoryType, ...] = (),
        include_history: bool = False,
        limit: int,
    ) -> list[MemoryRecord]:
        clauses = ["user_id = ?", "agent_id IS ?", "session_id IS ?"]
        params: list[object] = [scope.user_id, scope.agent_id, scope.session_id]
        if types:
            placeholders = ",".join("?" for _ in types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(t.value for t in types)
        if not include_history:
            clauses.append("validity = ?")
            params.append(Validity.ACTIVE.value)
        params.append(limit)
        query = (
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def query_superseded_memories(
        self,
        scope: MemoryScope,
        *,
        types: tuple[MemoryType, ...] = (),
        limit: int,
    ) -> list[MemoryRecord]:
        clauses = [
            "user_id = ?",
            "agent_id IS ?",
            "session_id IS ?",
            "validity = ?",
        ]
        params: list[object] = [
            scope.user_id,
            scope.agent_id,
            scope.session_id,
            Validity.SUPERSEDED.value,
        ]
        if types:
            placeholders = ",".join("?" for _ in types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(t.value for t in types)
        params.append(limit)
        query = (
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def query_temporal_memories(
        self,
        scope: MemoryScope,
        *,
        types: tuple[MemoryType, ...] = (),
        valid_at: datetime | None = None,
        created_before: datetime | None = None,
        created_after: datetime | None = None,
        limit: int,
    ) -> list[MemoryRecord]:
        clauses = [
            "user_id = ?",
            "agent_id IS ?",
            "session_id IS ?",
            "validity = ?",
        ]
        params: list[object] = [
            scope.user_id,
            scope.agent_id,
            scope.session_id,
            Validity.ACTIVE.value,
        ]
        if types:
            placeholders = ",".join("?" for _ in types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(t.value for t in types)
        if valid_at is not None:
            clauses.append("(valid_from IS NOT NULL AND valid_from <= ?)")
            params.append(valid_at.isoformat())
        if created_before is not None:
            clauses.append("created_at < ?")
            params.append(created_before.isoformat())
        if created_after is not None:
            clauses.append("created_at > ?")
            params.append(created_after.isoformat())
        params.append(limit)
        query = (
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)} "
            "ORDER BY valid_from DESC, created_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def get_relations_batch(
        self,
        memory_ids: tuple[str, ...],
        *,
        user_id: str,
    ) -> dict[str, list[MemoryRelation]]:
        result: dict[str, list[MemoryRelation]] = {mid: [] for mid in memory_ids}
        if not memory_ids:
            return result
        placeholders = ",".join("?" for _ in memory_ids)
        query = (
            "SELECT * FROM memory_relations "
            f"WHERE from_memory_id IN ({placeholders}) "
            f"OR to_memory_id IN ({placeholders}) "
            "ORDER BY created_at"
        )
        params: list[object] = [*memory_ids, *memory_ids]
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        if not rows:
            return result
        involved = {row["from_memory_id"] for row in rows} | {row["to_memory_id"] for row in rows}
        owned = {
            record.id
            for record in self.load_memories_by_ids(
                tuple(involved), user_id=user_id, limit=len(involved)
            )
        }
        for row in rows:
            if row["from_memory_id"] not in owned or row["to_memory_id"] not in owned:
                continue
            relation = self._relation_from_row(row)
            if row["from_memory_id"] in result:
                result[row["from_memory_id"]].append(relation)
            if row["to_memory_id"] in result:
                result[row["to_memory_id"]].append(relation)
        return result

    def list_unsynced_coverage(
        self,
        scope: MemoryScope,
        *,
        types: tuple[MemoryType, ...],
        limit: int,
    ) -> list[MemoryRecord]:
        type_placeholders = ",".join("?" for _ in types)
        query = (
            "SELECT DISTINCT m.* FROM memories m "
            "JOIN index_operations op ON op.memory_id = m.id "
            "WHERE m.user_id = ? AND m.agent_id IS ? AND m.session_id IS ? "
            f"AND m.type IN ({type_placeholders}) AND m.validity = ? "
            "AND op.kind = ? AND op.status IN (?, ?) "
            "ORDER BY m.updated_at DESC LIMIT ?"
        )
        params: list[object] = [
            scope.user_id,
            scope.agent_id,
            scope.session_id,
            *[t.value for t in types],
            Validity.ACTIVE.value,
            IndexOperationKind.UPSERT.value,
            IndexOperationStatus.PENDING.value,
            IndexOperationStatus.FAILED.value,
            limit,
        ]
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def revalidate_final_state(
        self,
        memory_ids: tuple[str, ...],
        *,
        user_id: str,
    ) -> list[MemoryRecord]:
        return self.load_memories_by_ids(memory_ids, user_id=user_id, limit=len(memory_ids))

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
            valid_from=(datetime.fromisoformat(row["valid_from"]) if row["valid_from"] else None),
            event_frame=(
                EventFrame.model_validate_json(row["event_frame_json"])
                if row["event_frame_json"]
                else None
            ),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _relation_from_row(row: sqlite3.Row) -> MemoryRelation:
        return MemoryRelation(
            from_memory_id=row["from_memory_id"],
            to_memory_id=row["to_memory_id"],
            relation=RelationKind(row["relation"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

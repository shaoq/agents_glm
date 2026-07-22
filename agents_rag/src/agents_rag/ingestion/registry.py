"""文档注册表（sqlite，真相源）。笔记 §2.3 / §8.2。

注册表持久化每个文档的 ``doc_id``、指纹、路径、``chunk_ids`` 反查表、版本、状态，
是五态 diff 与精确删改（按 ``chunk_id``）的根基，跨会话持久。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agents_rag.models import DocumentRecord, DocStatus, DocType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id            TEXT PRIMARY KEY,
    content_fingerprint TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    source_namespace  TEXT NOT NULL,
    doc_type          TEXT NOT NULL,
    chunk_ids         TEXT NOT NULL,
    parent_chunk_ids  TEXT NOT NULL,
    version           INTEGER NOT NULL,
    content_size      INTEGER NOT NULL,
    indexed_at        TEXT NOT NULL,
    status            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fingerprint ON documents(content_fingerprint);
CREATE INDEX IF NOT EXISTS idx_source_path ON documents(source_path);
"""


class DocumentRegistry:
    """文档注册表：持久化真相源，支持五态 diff。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DocumentRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert(self, rec: DocumentRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO documents (
                doc_id, content_fingerprint, source_path, source_namespace,
                doc_type, chunk_ids, parent_chunk_ids, version, content_size,
                indexed_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                content_fingerprint = excluded.content_fingerprint,
                source_path        = excluded.source_path,
                source_namespace   = excluded.source_namespace,
                doc_type           = excluded.doc_type,
                chunk_ids          = excluded.chunk_ids,
                parent_chunk_ids   = excluded.parent_chunk_ids,
                version            = excluded.version,
                content_size       = excluded.content_size,
                indexed_at         = excluded.indexed_at,
                status             = excluded.status
            """,
            (
                rec.doc_id,
                rec.content_fingerprint,
                rec.source_path,
                rec.source_namespace,
                rec.doc_type.value,
                json.dumps(list(rec.chunk_ids)),
                json.dumps(list(rec.parent_chunk_ids)),
                rec.version,
                rec.content_size,
                rec.indexed_at.isoformat(),
                rec.status.value,
            ),
        )
        self._conn.commit()

    def get(self, doc_id: str) -> DocumentRecord | None:
        row = self._conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def delete(self, doc_id: str) -> None:
        self._conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self._conn.commit()

    def list(self) -> list[DocumentRecord]:
        rows = self._conn.execute("SELECT * FROM documents").fetchall()
        return [self._row_to_record(r) for r in rows]

    def all_records(self) -> dict[str, DocumentRecord]:
        """``doc_id -> record``，供 diff 使用。"""
        return {r.doc_id: r for r in self.list()}

    @staticmethod
    def _row_to_record(row: tuple) -> DocumentRecord:
        return DocumentRecord(
            doc_id=row[0],
            content_fingerprint=row[1],
            source_path=row[2],
            source_namespace=row[3],
            doc_type=DocType(row[4]),
            chunk_ids=tuple(json.loads(row[5])),
            parent_chunk_ids=tuple(json.loads(row[6])),
            version=row[7],
            content_size=row[8],
            indexed_at=row[9],
            status=DocStatus(row[10]),
        )

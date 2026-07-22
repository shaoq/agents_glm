"""原图文档维度存储 + ImageRecord 注册表。笔记 §12.1.1。

``storage/images/<doc_id>/<image_id>.<format>``（``image_id = content_hash``，``format``
由 magic bytes 检测）；``ImageRecord`` sqlite 注册表支撑图片级增量 + 格式记录
（mime: ``image/{format}``）。删除文档 = 删整个 ``<doc_id>/`` 目录 + 注册表记录。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from agents_rag.models import ImageRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    image_id      TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    page          INTEGER,
    caption       TEXT,
    description   TEXT NOT NULL,
    format        TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc  ON images(doc_id);
CREATE INDEX IF NOT EXISTS idx_hash ON images(content_hash);
"""


def image_content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_format(data: bytes) -> str:
    """从 magic bytes 检测图片格式（用于文件扩展名与 mime: image/{format}）。默认 png。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return "png"


def _safe(name: str) -> str:
    return name.replace(":", "_").replace("/", "_")


def _row_to_record(row: tuple) -> ImageRecord:
    return ImageRecord(
        image_id=row[0],
        doc_id=row[1],
        source_path=row[2],
        page=row[3],
        caption=row[4],
        description=row[5],
        format=row[6],
        content_hash=row[7],
        created_at=datetime.fromisoformat(row[8]),
    )


class ImageStore:
    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._root / "images.sqlite")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ImageStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _doc_dir(self, doc_id: str) -> Path:
        return self._root / _safe(doc_id)

    def path_of(self, doc_id: str, image_ref: str) -> Path:
        """``image_ref`` = 文件名（含扩展，如 ``<hash>.png``）。"""
        return self._doc_dir(doc_id) / image_ref

    def put(self, data: bytes, doc_id: str, fmt: str) -> str:
        """存原图，返回 ``image_ref``（文件名 = ``<content_hash>.<fmt>``）。幂等。"""
        image_id = image_content_hash(data)
        image_ref = f"{image_id}.{fmt}"
        d = self._doc_dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / image_ref
        if not path.exists():
            path.write_bytes(data)
        return image_ref

    def get(self, doc_id: str, image_ref: str) -> bytes | None:
        p = self.path_of(doc_id, image_ref)
        return p.read_bytes() if p.exists() else None

    def upsert_record(self, rec: ImageRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO images (image_id, doc_id, source_path, page, caption,
                                description, format, content_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id) DO UPDATE SET
                description = excluded.description,
                caption     = excluded.caption,
                format      = excluded.format
            """,
            (
                rec.image_id,
                rec.doc_id,
                rec.source_path,
                rec.page,
                rec.caption,
                rec.description,
                rec.format,
                rec.content_hash,
                rec.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_record(self, image_id: str) -> ImageRecord | None:
        row = self._conn.execute(
            "SELECT * FROM images WHERE image_id = ?", (image_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def find_by_hash(self, content_hash: str) -> ImageRecord | None:
        """按 content_hash 查（图片级增量：图没变则复用既有描述）。"""
        row = self._conn.execute(
            "SELECT * FROM images WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def list_by_doc(self, doc_id: str) -> list[ImageRecord]:
        rows = self._conn.execute(
            "SELECT * FROM images WHERE doc_id = ?", (doc_id,)
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def delete_by_doc(self, doc_id: str) -> None:
        d = self._doc_dir(doc_id)
        if d.exists():
            shutil.rmtree(d)
        self._conn.execute("DELETE FROM images WHERE doc_id = ?", (doc_id,))
        self._conn.commit()

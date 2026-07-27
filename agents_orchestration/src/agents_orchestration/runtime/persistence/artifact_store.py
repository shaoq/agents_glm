"""Content-addressed immutable Artifact Store (tasks 3.7 / 3.8).

Content is written to disk first; the resulting ``ArtifactRef`` is recorded as
metadata inside the SQLite transaction (task 3.6). If that transaction rolls
back, the file remains on disk as a content-addressed orphan — safe, because no
state references it, and reclaimable via :meth:`list_orphans` /
:meth:`delete_orphan` (task 3.8).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef, hash_content
from agents_orchestration.runtime.persistence.mappers import dump, load
from agents_orchestration.runtime.ports import OrphanArtifactError


def _hash_to_filename(content_hash: str) -> str:
    return content_hash.replace(":", "_") + ".bin"


def _filename_to_hash(filename: str) -> str:
    stem = filename[:-4] if filename.endswith(".bin") else filename
    return stem.replace("_", ":", 1)


class SqliteArtifactStore:
    """Filesystem + metadata-table backed ArtifactStore."""

    def __init__(self, conn: sqlite3.Connection, artifact_dir: Path) -> None:
        self.conn = conn
        self.dir = artifact_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        content: bytes,
        *,
        kind: ArtifactKind,
        artifact_id: str | None = None,
    ) -> ArtifactRef:
        content_hash = hash_content(content)
        existing = self.find(content_hash)
        if existing is not None:
            return existing
        filename = _hash_to_filename(content_hash)
        (self.dir / filename).write_bytes(content)
        artifact_id = artifact_id or f"{kind.value}_{content_hash[-16:]}"
        return ArtifactRef(
            artifact_id=artifact_id,
            content_hash=content_hash,
            path=filename,
            size_bytes=len(content),
            kind=kind,
        )

    def read(self, ref: ArtifactRef) -> bytes:
        path = self.dir / ref.path
        if not path.exists():
            raise OrphanArtifactError(f"artifact file missing: {ref.path}")
        data = path.read_bytes()
        if hash_content(data) != ref.content_hash:
            raise OrphanArtifactError(f"artifact content hash mismatch: {ref.path}")
        return data

    def find(self, content_hash: str) -> ArtifactRef | None:
        row = self.conn.execute(
            "SELECT data FROM artifact_metadata WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        return load(ArtifactRef, row["data"]) if row else None

    def get_by_id(self, artifact_id: str) -> ArtifactRef | None:
        row = self.conn.execute(
            "SELECT data FROM artifact_metadata WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return load(ArtifactRef, row["data"]) if row else None

    def list_all(self) -> list[ArtifactRef]:
        rows = self.conn.execute("SELECT data FROM artifact_metadata")
        return [load(ArtifactRef, row["data"]) for row in rows]

    def record_metadata(self, ref: ArtifactRef) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO artifact_metadata "
            "(artifact_id, content_hash, path, kind, data) VALUES (?, ?, ?, ?, ?)",
            (ref.artifact_id, ref.content_hash, ref.path, ref.kind.value, dump(ref)),
        )

    def referenced_hashes(self) -> set[str]:
        return {
            row["content_hash"]
            for row in self.conn.execute("SELECT content_hash FROM artifact_metadata")
        }

    def list_orphans(self) -> list[Path]:
        referenced = self.referenced_hashes()
        orphans: list[Path] = []
        for path in self.dir.glob("*.bin"):
            if _filename_to_hash(path.name) not in referenced:
                orphans.append(path)
        return orphans

    def delete_orphan(self, path: Path) -> None:
        referenced = self.referenced_hashes()
        if _filename_to_hash(path.name) in referenced:
            raise OrphanArtifactError(f"refused to delete referenced artifact: {path.name}")
        path.unlink()

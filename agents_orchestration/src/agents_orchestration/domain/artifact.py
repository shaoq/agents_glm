"""Immutable, content-addressed Artifact references (design Decision 4/14).

Large content is written to the Artifact Store first; the resulting
content-hashed ``ArtifactRef`` is then carried into a SQLite transaction, so a
failed transaction never produces a referenced-but-missing artifact and a
successful transaction never references an unwritten one (task 3.7/3.8).
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from agents_orchestration.domain.ids import ArtifactId


class ArtifactKind(StrEnum):
    """Logical kind of artifact."""

    EVIDENCE = "evidence"
    ANALYSIS = "analysis"
    REPORT_MARKDOWN = "report_markdown"
    REPORT_JSON = "report_json"
    RUN_SUMMARY = "run_summary"
    PLAN_PROPOSAL = "plan_proposal"
    REVIEW = "review"
    CHECKPOINT = "checkpoint"
    OTHER = "other"


def hash_content(data: bytes) -> str:
    """Return the ``sha256:`` -prefixed content hash for ``data``."""

    return "sha256:" + hashlib.sha256(data).hexdigest()


class ArtifactRef(BaseModel):
    """An immutable reference to a stored artifact."""

    model_config = ConfigDict(frozen=True)

    artifact_id: ArtifactId
    content_hash: str
    path: str
    size_bytes: int
    kind: ArtifactKind = ArtifactKind.OTHER
    created_at: str | None = None

    def verify(self, data: bytes) -> bool:
        """True if ``data`` hashes to this ref's ``content_hash``."""

        return hash_content(data) == self.content_hash

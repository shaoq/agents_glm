"""Content-addressed AnalysisArtifact persistence + authoritative handoff
(analyze-sufficiency-feedback Decision 5 / tasks 3.1-3.3).

A candidate ``AnalysisArtifact`` accepted by the ANALYZE phase (``sufficient`` or
``conflict``) is materialized as an immutable, content-addressed artifact. The
ACCEPTED ANALYZE Stage's ``output_artifact_refs`` is the SOLE authority that
declares it the current Plan's analysis: WRITING / REVIEW / FINALIZE providers
load that artifact and MUST NOT re-invoke the analyst.

A materialized-but-unreferenced blob (stage CAS failed) is an unreferenced orphan
that no provider can select; it is reclaimable by orphan cleanup (task 3.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef
from agents_orchestration.domain.coordination import StageStatus
from agents_orchestration.orchestration.report import AnalysisArtifact

ANALYZE_LOGICAL_STAGE_KEY = "analyze"


@dataclass(frozen=True)
class AnalysisArtifactRef:
    """A content-addressed reference to a materialized AnalysisArtifact (task 3.1).

    ``artifact_id`` is the Analysis entity id (content-addressed, so identical
    content yields an identical id); ``content_hash`` lets consumers verify they
    read exactly what was accepted. ``run_id`` / ``plan_version`` bind it to the
    plan it was accepted for, and ``source_evidence_hash`` binds it to the
    EvidenceSet it was reviewed against (observability, task 9.1).
    """

    artifact_id: str
    content_hash: str
    run_id: str
    plan_version: int
    source_evidence_hash: str
    path: str
    size_bytes: int

    def as_artifact_ref(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            content_hash=self.content_hash,
            path=self.path,
            size_bytes=self.size_bytes,
            kind=ArtifactKind.ANALYSIS,
        )


class MissingAcceptedAnalysisError(RuntimeError):
    """No ACCEPTED ANALYZE artifact is referenced for the current Plan (task 3.4).

    Raised by the authoritative loader so WRITING fails explicitly instead of
    silently re-running the analyst or fabricating an Analysis.
    """


class AnalysisArtifactStore(Protocol):
    """Materialize/load a content-addressed AnalysisArtifact (task 3.1)."""

    async def materialize(
        self,
        *,
        run_id: str,
        plan_version: int,
        analysis: AnalysisArtifact,
        source_evidence_hash: str,
    ) -> AnalysisArtifactRef: ...

    async def load(self, ref: AnalysisArtifactRef) -> AnalysisArtifact: ...


class SqliteAnalysisArtifactStore:
    """:class:`AnalysisArtifactStore` over a :class:`SqliteArtifactStore`."""

    def __init__(self, artifact_store) -> None:
        self.store = artifact_store

    async def materialize(
        self,
        *,
        run_id: str,
        plan_version: int,
        analysis: AnalysisArtifact,
        source_evidence_hash: str,
    ) -> AnalysisArtifactRef:
        content = analysis.model_dump_json().encode("utf-8")
        ref = self.store.write(content, kind=ArtifactKind.ANALYSIS)
        return AnalysisArtifactRef(
            artifact_id=ref.artifact_id,
            content_hash=ref.content_hash,
            run_id=run_id,
            plan_version=plan_version,
            source_evidence_hash=source_evidence_hash,
            path=ref.path,
            size_bytes=ref.size_bytes,
        )

    async def load(self, ref: AnalysisArtifactRef) -> AnalysisArtifact:
        data = self.store.read(ref.as_artifact_ref())
        return AnalysisArtifact.model_validate_json(data)


def accepted_analysis_ref(stages, run_id: str, plan_version: int) -> AnalysisArtifactRef | None:
    """The ACCEPTED ANALYZE artifact ref for ``plan_version``, or None (task 3.2).

    The ACCEPTED Stage's ``output_artifact_refs`` is the sole authority: a
    materialized blob that no ACCEPTED Stage references is invisible here (an
    orphan). Among accepted ANALYZE stages, the latest one bound to the current
    plan version wins — a candidate accepted for an older plan is not loadable.
    """

    for stage in reversed(stages.for_logical_stage(run_id, ANALYZE_LOGICAL_STAGE_KEY)):
        if stage.status is not StageStatus.ACCEPTED:
            continue
        if stage.fingerprint.plan_version != plan_version:
            continue
        if not stage.output_artifact_refs:
            continue
        blob = stage.output_artifact_refs[0]
        evidence_prefix = "source_evidence_hash:"
        evidence_hash = next(
            (
                entity[len(evidence_prefix) :]
                for entity in stage.output_entity_ids
                if entity.startswith(evidence_prefix)
            ),
            "",
        )
        return AnalysisArtifactRef(
            artifact_id=blob.artifact_id,
            content_hash=blob.content_hash,
            run_id=run_id,
            plan_version=plan_version,
            source_evidence_hash=evidence_hash,
            path=blob.path,
            size_bytes=blob.size_bytes,
        )
    return None


__all__ = [
    "ANALYZE_LOGICAL_STAGE_KEY",
    "AnalysisArtifactRef",
    "AnalysisArtifactStore",
    "MissingAcceptedAnalysisError",
    "SqliteAnalysisArtifactStore",
    "accepted_analysis_ref",
]

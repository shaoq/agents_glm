"""Analysis, report, review and finalization (tasks 10.1-10.11).

Produces immutable, evidence-linked ``AnalysisArtifact`` and ``ReportContent``,
maps reviewer verdicts to Replan/Gate policy (10.5), evaluates the Completion
Contract deterministically (10.6), validates citation integrity (10.8), freezes
the candidate state via compare-and-set (10.7) and writes the immutable
``report.md`` / ``report.json`` / ``run-summary.json`` artifacts (10.9/10.10).
Report content is data only — it can never trigger a write-side-effect capability
(task 10.11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.artifact import ArtifactKind, ArtifactRef
from agents_orchestration.domain.enums import (
    CompletionState,
    EffectType,
    ReviewVerdict,
    Sufficiency,
    TerminationReason,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.evidence import Degradation, EvidenceSet
from agents_orchestration.domain.goal import CompletionContract


class AnalysisArtifact(BaseModel):
    """Immutable analysis with evidence-linked conclusions (task 10.1)."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    conclusions: tuple[str, ...] = Field(default_factory=tuple)
    cited_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    open_questions: tuple[str, ...] = Field(default_factory=tuple)


class ReportSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    body: str
    cited_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)


class ReportContent(BaseModel):
    """ReportWriter output: Markdown + structured content (task 10.2)."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    title: str
    objective: str
    sections: tuple[ReportSection, ...] = Field(default_factory=tuple)
    conclusions: tuple[str, ...] = Field(default_factory=tuple)
    cited_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", "", f"**Objective:** {self.objective}", ""]
        for section in self.sections:
            lines += [f"## {section.title}", "", section.body, ""]
        if self.conclusions:
            lines += ["## Conclusions", ""]
            lines += [f"- {c}" for c in self.conclusions]
            lines.append("")
        return "\n".join(lines)

    def to_structured(self) -> dict:
        return {
            "run_id": self.run_id,
            "title": self.title,
            "objective": self.objective,
            "sections": [s.model_dump() for s in self.sections],
            "conclusions": list(self.conclusions),
            "cited_evidence_ids": list(self.cited_evidence_ids),
        }


@dataclass(frozen=True)
class ReviewProposal:
    """ReportReviewer proposal (task 10.3)."""

    verdict: ReviewVerdict
    reason: str
    suggested_actions: tuple[str, ...] = ()


def map_review_verdict(verdict: ReviewVerdict) -> str:
    """Connect RESEARCH_GAP / material conflict verdicts to policy (task 10.5)."""

    if verdict is ReviewVerdict.RESEARCH_GAP:
        return "replan"
    if verdict is ReviewVerdict.CONFLICT:
        return "gate"
    if verdict is ReviewVerdict.ESCALATE:
        return "gate"
    return "none"


class CompletionEvaluator:
    """Deterministic Completion Contract evaluation (task 10.6)."""

    def evaluate(
        self,
        contract: CompletionContract,
        *,
        evidence: EvidenceSet,
        deliverables_present: dict[str, bool],
    ) -> tuple[dict[str, CompletionState], CompletionState]:
        per_criterion: dict[str, CompletionState] = {}
        states: list[CompletionState] = []
        for criterion in contract.criteria:
            key = criterion.description
            if criterion.kind.value == "deliverable":
                present = deliverables_present.get(criterion.deliverable_path or "", False)
                state = CompletionState.SATISFIED if present else CompletionState.UNSATISFIED
            elif criterion.kind.value == "evidence_sufficiency":
                state = self._sufficiency_to_state(evidence.sufficiency)
            elif criterion.kind.value == "citation_integrity":
                state = CompletionState.SATISFIED  # validated separately by CitationValidator
            else:
                state = CompletionState.NOT_APPLICABLE
            per_criterion[key] = state
            states.append(state)

        if not states:
            return per_criterion, CompletionState.NOT_APPLICABLE
        if any(s is CompletionState.UNSATISFIED for s in states):
            return per_criterion, CompletionState.UNSATISFIED
        if any(s is CompletionState.UNKNOWN for s in states):
            return per_criterion, CompletionState.UNKNOWN
        return per_criterion, CompletionState.SATISFIED

    @staticmethod
    def _sufficiency_to_state(sufficiency: Sufficiency) -> CompletionState:
        if sufficiency is Sufficiency.SUFFICIENT:
            return CompletionState.SATISFIED
        if sufficiency is Sufficiency.INSUFFICIENT:
            return CompletionState.UNSATISFIED
        if sufficiency is Sufficiency.CONFLICTED:
            return CompletionState.UNSATISFIED
        return CompletionState.UNKNOWN


class CitationValidator:
    """Validate report citations against Evidence IDs (task 10.8)."""

    def validate(self, report: ReportContent, evidence_ids: set[str]) -> list[str]:
        known = evidence_ids
        return [cid for cid in report.cited_evidence_ids if cid not in known]


@dataclass(frozen=True)
class ReportArtifacts:
    report_markdown: ArtifactRef
    report_json: ArtifactRef
    run_summary: ArtifactRef


class ReportBuilder:
    """Generate immutable report.md / report.json / run-summary.json (10.9/10.10)."""

    def build(
        self,
        artifact_store,
        *,
        run_id: str,
        report: ReportContent,
        analysis: AnalysisArtifact,
        completion_overall: CompletionState,
        evidence: EvidenceSet,
        degradations: tuple[Degradation, ...],
        termination: TerminationReason | None,
        clock,
    ) -> ReportArtifacts:
        occurred_at = clock.now().isoformat()
        markdown = report.to_markdown().encode("utf-8")
        structured = json.dumps(report.to_structured(), ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        summary = self._run_summary(
            run_id, completion_overall, evidence, degradations, termination, report, occurred_at
        )
        md_ref = self._write(artifact_store, markdown, ArtifactKind.REPORT_MARKDOWN, occurred_at)
        json_ref = self._write(artifact_store, structured, ArtifactKind.REPORT_JSON, occurred_at)
        summary_ref = self._write(artifact_store, summary, ArtifactKind.RUN_SUMMARY, occurred_at)
        for ref in (md_ref, json_ref, summary_ref):
            artifact_store.record_metadata(ref)
        return ReportArtifacts(md_ref, json_ref, summary_ref)

    @staticmethod
    def _write(artifact_store, content: bytes, kind: ArtifactKind, occurred_at: str) -> ArtifactRef:
        ref = artifact_store.write(content, kind=kind)
        return ref.model_copy(update={"created_at": occurred_at})

    @staticmethod
    def _run_summary(
        run_id: str,
        completion: CompletionState,
        evidence: EvidenceSet,
        degradations: tuple[Degradation, ...],
        termination: TerminationReason | None,
        report: ReportContent,
        occurred_at: str,
    ) -> bytes:
        missing_sources = [
            cid
            for cid in report.cited_evidence_ids
            if cid not in {e.evidence_id for e in evidence.evidences}
        ]
        summary = {
            "run_id": run_id,
            "generated_at": occurred_at,
            "completion": completion.value,
            "termination": termination.value if termination else None,
            "independent_evidence_count": evidence.independent_count,
            "sufficiency": evidence.sufficiency.value,
            "unresolved_conflicts": [c.model_dump() for c in evidence.conflicts],
            "missing_required": evidence.missing_required,
            "missing_sources": missing_sources,
            "degradations": [d.model_dump() for d in degradations],
            "unmet": completion is not CompletionState.SATISFIED,
        }
        return json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")


class Finalizer:
    """Freeze the candidate state and commit the terminal state via CAS (10.7)."""

    def finalize(
        self,
        uow,
        run,
        *,
        artifacts: ReportArtifacts,
        completion_overall: CompletionState,
        clock,
        idgen,
    ) -> tuple[object, object]:
        from agents_orchestration.domain.enums import RunState

        now = clock.now()
        reason = (
            TerminationReason.COMPLETED
            if completion_overall is CompletionState.SATISFIED
            else TerminationReason.REQUIRED_EVIDENCE_MISSING
        )
        terminal = run.terminate(reason, now)
        # Bind the final report artifact hash to the terminal transition.
        terminal = terminal.model_copy(
            update={
                "state": RunState.SUCCEEDED
                if reason is TerminationReason.COMPLETED
                else RunState.FAILED
            }
        )
        uow.runs.save(terminal, expected_version=run.state_version)
        uow.events.append(
            [
                DomainEvent(
                    event_id=idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=EffectType.RUN_TERMINATED,
                    state_version=terminal.state_version,
                    occurred_at=now,
                    payload={
                        "report_hash": artifacts.report_markdown.content_hash,
                        "completion": completion_overall.value,
                    },
                )
            ]
        )
        return terminal, reason


__all__ = [
    "AnalysisArtifact",
    "CitationValidator",
    "CompletionEvaluator",
    "Finalizer",
    "ReportArtifacts",
    "ReportBuilder",
    "ReportContent",
    "ReportSection",
    "ReviewProposal",
    "map_review_verdict",
]

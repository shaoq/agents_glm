"""Context assembly and result sufficiency.

Renders selected evidence groups into a stable, traceable context: each entry
carries its role, content and evidence id; conflict sides use neutral wording.
Determines sufficiency (SUFFICIENT / PARTIAL / CONFLICTED / EMPTY) from the
selected evidence. No generative summarization; every context line maps to a
structured evidence item.

Reference: design 11 (context assembly and result status).
"""

from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    ContextAssembly,
    EvidenceGroup,
    RecallLane,
    RecallRequest,
    Sufficiency,
)


def _render_item(item) -> str:
    note = f" (id:{item.evidence_id}"
    if item.valid_from is not None:
        note += f", valid_from:{item.valid_from.date().isoformat()}"
    note += ")"
    return f"[{item.role.value}] {item.content}{note}"


def _render_group(group: EvidenceGroup) -> str:
    lines = [_render_item(group.primary)]
    for item in group.supporting:
        lines.append(_render_item(item))
    for item in group.historical:
        lines.append(_render_item(item))
    for item in group.conflicting:
        lines.append(_render_item(item))
    return "\n".join(lines)


def _assess_sufficiency(groups: tuple[EvidenceGroup, ...]) -> Sufficiency:
    if not groups:
        return Sufficiency.EMPTY
    if any(group.conflicting for group in groups):
        return Sufficiency.CONFLICTED
    if not any(group.primary.role.value == "current" for group in groups):
        return Sufficiency.PARTIAL
    return Sufficiency.SUFFICIENT


class ContextAssembler:
    """Renders evidence groups into a faithful, traceable context."""

    def assemble(
        self,
        request: RecallRequest,
        groups: tuple[EvidenceGroup, ...],
        diag: RecallDiagnostics,  # noqa: ARG002 (deterministic; protocol symmetry)
    ) -> ContextAssembly:
        if not groups:
            return ContextAssembly(
                context="",
                sufficiency=Sufficiency.EMPTY,
                intent_summary=request.query,
                lanes_used=(),
            )
        sections = [_render_group(group) for group in groups]
        return ContextAssembly(
            context="\n\n".join(sections),
            sufficiency=_assess_sufficiency(groups),
            intent_summary=request.query,
            lanes_used=(RecallLane.SESSION_CURRENT,),  # refined when wired through pipeline
        )

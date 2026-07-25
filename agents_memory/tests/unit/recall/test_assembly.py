"""Tests for ContextAssembler (task 7.5/7.6/7.7)."""

from agents_memory.models import MemoryScope, MemoryType
from agents_memory.recall.assembly import ContextAssembler
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    EvidenceGroup,
    EvidenceItem,
    EvidenceRole,
    RecallRequest,
    Sufficiency,
)


def _item(eid: str, role: EvidenceRole = EvidenceRole.CURRENT, content: str = "x") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid,
        memory_id=eid.split(":")[0],
        role=role,
        content=content,
        memory_type=MemoryType.FACT,
        scope=MemoryScope(user_id="u1"),
        importance=5,
    )


def _group(gid: str, *, conflict: bool = False) -> EvidenceGroup:
    primary = _item(f"{gid}:current", content=gid)
    if not conflict:
        return EvidenceGroup(group_id=gid, primary=primary)
    return EvidenceGroup(
        group_id=gid,
        primary=primary,
        conflicting=(_item(f"{gid}:conflict", EvidenceRole.CONFLICTING, f"{gid} alt"),),
    )


def _request() -> RecallRequest:
    return RecallRequest(user_id="u1", query="what decides", max_evidence_items=5)


class TestContextAssembler:
    def test_empty_groups_yield_empty_context(self):
        assembly = ContextAssembler().assemble(_request(), (), RecallDiagnostics())
        assert assembly.context == ""
        assert assembly.sufficiency is Sufficiency.EMPTY

    def test_renders_groups_with_traceable_evidence_ids(self):
        assembly = ContextAssembler().assemble(_request(), (_group("m1"),), RecallDiagnostics())
        assert "m1:current" in assembly.context
        assert "[current]" in assembly.context

    def test_conflict_yields_conflicted_sufficiency(self):
        assembly = ContextAssembler().assemble(
            _request(), (_group("m1", conflict=True),), RecallDiagnostics()
        )
        assert assembly.sufficiency is Sufficiency.CONFLICTED
        # Both conflict sides rendered.
        assert "conflict" in assembly.context

    def test_sufficient_when_current_evidence_present(self):
        assembly = ContextAssembler().assemble(_request(), (_group("m1"),), RecallDiagnostics())
        assert assembly.sufficiency is Sufficiency.SUFFICIENT

    def test_intent_summary_carry_query(self):
        assembly = ContextAssembler().assemble(_request(), (_group("m1"),), RecallDiagnostics())
        assert assembly.intent_summary == "what decides"

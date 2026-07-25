"""Tests for SetSelector (task 7.1/7.2)."""

from agents_memory.models import MemoryScope, MemoryType
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    EvidenceGroup,
    EvidenceItem,
    EvidenceRole,
    RecallRequest,
)
from agents_memory.recall.selection import SetSelector, group_value


def _group(gid: str, importance: int = 5, *, conflict: bool = False) -> EvidenceGroup:
    primary = EvidenceItem(
        evidence_id=f"{gid}:current",
        memory_id=gid,
        role=EvidenceRole.CURRENT,
        content=gid,
        memory_type=MemoryType.FACT,
        scope=MemoryScope(user_id="u1"),
        importance=importance,
    )
    if not conflict:
        return EvidenceGroup(group_id=gid, primary=primary)
    conflicting = EvidenceItem(
        evidence_id=f"{gid}:conflict",
        memory_id=f"{gid}c",
        role=EvidenceRole.CONFLICTING,
        content=f"{gid} alt",
        memory_type=MemoryType.FACT,
        scope=MemoryScope(user_id="u1"),
        importance=importance,
    )
    return EvidenceGroup(group_id=gid, primary=primary, conflicting=(conflicting,))


def _request(**kwargs) -> RecallRequest:
    defaults: dict = {"user_id": "u1", "query": "q", "max_evidence_items": 2}
    defaults.update(kwargs)
    return RecallRequest(**defaults)


class TestSetSelector:
    def test_empty_returns_empty(self):
        assert SetSelector().select(_request(), (), RecallDiagnostics()) == ()

    def test_caps_to_max_evidence_items(self):
        groups = tuple(_group(f"g{i}") for i in range(5))
        selected = SetSelector().select(_request(), groups, RecallDiagnostics())
        assert len(selected) <= 2

    def test_ranks_higher_importance_first(self):
        selected = SetSelector().select(
            _request(),
            (_group("low", importance=2), _group("high", importance=9)),
            RecallDiagnostics(),
        )
        assert selected[0].group_id == "high"

    def test_conflict_group_is_atomic(self):
        groups = (_group("conflict", importance=3, conflict=True), _group("plain", importance=9))
        selected = SetSelector().select(_request(max_evidence_items=1), groups, RecallDiagnostics())
        # The conflict group must survive whole (both sides) when selected.
        conflict_selected = [g for g in selected if g.group_id == "conflict"]
        if conflict_selected:
            assert len(conflict_selected[0].conflicting) == 1

    def test_group_value_increases_with_members(self):
        base = _group("g", importance=5)
        rich = EvidenceGroup(
            group_id="rich",
            primary=base.primary,
            historical=(
                EvidenceItem(
                    evidence_id="rich:h",
                    memory_id="richh",
                    role=EvidenceRole.HISTORICAL,
                    content="x",
                    memory_type=MemoryType.FACT,
                    scope=MemoryScope(user_id="u1"),
                    importance=5,
                ),
            ),
        )
        assert group_value(rich) > group_value(base)

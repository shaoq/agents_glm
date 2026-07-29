"""Unit tests for source-hint → capability mapping (task 2.5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_orchestration.domain.enums import BranchRole, CapabilityKind
from agents_orchestration.orchestration.llm_ports import (
    _SOURCE_HINT_MAP,
    _TaskSpecOut,
    map_source_hints,
)


def test_map_normal_multi_source():
    result = map_source_hints(["local_knowledge", "live_web"], web_enabled=True)
    assert result == (
        (CapabilityKind.RAG_SEARCH, BranchRole.REQUIRED),
        (CapabilityKind.WEB_RESEARCH, BranchRole.OPTIONAL),
    )


def test_map_all_three_sources():
    result = map_source_hints(["local_knowledge", "personal_context", "live_web"], web_enabled=True)
    assert [c for c, _ in result] == [
        CapabilityKind.RAG_SEARCH,
        CapabilityKind.MEMORY_RECALL,
        CapabilityKind.WEB_RESEARCH,
    ]


def test_map_web_filtered_when_disabled():
    result = map_source_hints(["local_knowledge", "live_web"], web_enabled=False)
    assert result == ((CapabilityKind.RAG_SEARCH, BranchRole.REQUIRED),)


def test_map_empty_hints_fallback_local_knowledge():
    result = map_source_hints([], web_enabled=True)
    assert result == ((CapabilityKind.RAG_SEARCH, BranchRole.REQUIRED),)


def test_map_unknown_labels_ignored():
    result = map_source_hints(["local_knowledge", "bogus"], web_enabled=True)
    assert result == ((CapabilityKind.RAG_SEARCH, BranchRole.REQUIRED),)


def test_map_all_filtered_returns_empty():
    # live_web disabled + only live_web requested → empty (caller records Degradation)
    result = map_source_hints(["live_web"], web_enabled=False)
    assert result == ()


def test_map_dedupes_duplicate_capability():
    result = map_source_hints(["local_knowledge", "local_knowledge"], web_enabled=True)
    assert result == ((CapabilityKind.RAG_SEARCH, BranchRole.REQUIRED),)


def test_source_hint_map_covers_all_documented_labels():
    assert set(_SOURCE_HINT_MAP) == {"local_knowledge", "personal_context", "live_web"}


def test_task_spec_out_source_hints_accepts_valid_labels():
    spec = _TaskSpecOut(
        task_id="r1",
        role="evidence_researcher",
        description="d",
        source_hints=["local_knowledge", "live_web"],
    )
    assert spec.source_hints == ["local_knowledge", "live_web"]


def test_task_spec_out_source_hints_defaults_empty():
    spec = _TaskSpecOut(task_id="r2", role="evidence_researcher", description="d")
    assert spec.source_hints == []


def test_task_spec_out_source_hints_rejects_non_enum_value():
    # Literal enum constraint: a raw capability name must be rejected
    with pytest.raises(ValidationError):
        _TaskSpecOut(
            task_id="r3", role="evidence_researcher", description="d", source_hints=["rag"]
        )

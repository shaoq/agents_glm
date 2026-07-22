"""models 数据结构测试（不可变、默认值、序列化往返、派生方法）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_rag.models import (
    Action,
    ActionKind,
    Block,
    BlockType,
    ChildChunk,
    Document,
    DocumentRecord,
    DocType,
    QualityReport,
    ScanItem,
    Section,
)


def test_block_is_frozen():
    b = Block(type=BlockType.PARAGRAPH, text="hi")
    with pytest.raises(ValidationError):
        b.text = "x"  # type: ignore[misc]


def test_child_chunk_metadata_dict_has_primitive_types():
    c = ChildChunk(
        id="p1__0",
        parent_id="p1",
        doc_id="d1",
        text="t",
        page=3,
        heading="H",
        section_path="A > B",
        block_type=BlockType.TABLE,
        char_span=(0, 5),
    )
    md = c.metadata_dict()
    assert md["page"] == 3
    assert md["block_type"] == "table"
    assert md["status"] == "active"
    assert isinstance(md["page"], int)
    assert isinstance(md["block_type"], str)


def test_child_chunk_id_encodes_parent():
    c = ChildChunk(id="p1__2", parent_id="p1", doc_id="d", text="t")
    assert c.id.startswith("p1__")
    assert c.version == 1
    assert c.status.value == "active"


def test_quality_report_is_poor_detection():
    assert QualityReport(chars_per_page=10.0).is_poor()  # 疑似扫描件
    assert QualityReport(chars_per_page=200.0, garbage_ratio=0.5).is_poor()
    assert not QualityReport(chars_per_page=200.0, garbage_ratio=0.1).is_poor()


def test_document_iter_blocks_nested():
    inner = Section(heading="inner", blocks=(Block(text="b2"),))
    outer = Section(
        heading="outer", blocks=(Block(text="b1"),), children=(inner,)
    )
    doc = Document(
        doc_id="d", source="s", doc_type=DocType.MARKDOWN, sections=(outer,)
    )
    assert [b.text for b in doc.iter_blocks()] == ["b1", "b2"]


def test_record_defaults_and_action_roundtrip():
    rec = DocumentRecord(doc_id="d1", content_fingerprint="fp", source_path="/a")
    assert rec.version == 1
    assert rec.status.value == "active"

    a = Action(
        kind=ActionKind.NEW,
        doc_id="d1",
        source_path="/a",
        fingerprint="fp",
        doc_type=DocType.MARKDOWN,
    )
    a2 = Action.model_validate(a.model_dump())
    assert a2 == a  # 序列化往返


def test_scan_item_is_frozen():
    si = ScanItem(
        source_path="/a",
        doc_type=DocType.TXT,
        content_fingerprint="fp",
        content_size=10,
        mtime=1.0,
    )
    with pytest.raises(ValidationError):
        si.source_path = "/b"  # type: ignore[misc]

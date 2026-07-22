"""ingestion 测试：指纹/预筛、注册表持久化、五态 diff、两阶段执行。"""

from __future__ import annotations

import hashlib

import pytest

from agents_rag.ingestion.actions import execute, order_actions
from agents_rag.ingestion.collector import diff, make_doc_id, scan_directory
from agents_rag.ingestion.fingerprint import (
    file_fingerprint,
    file_stat,
    maybe_changed,
    text_fingerprint,
)
from agents_rag.ingestion.registry import DocumentRegistry
from agents_rag.models import Action, ActionKind, DocumentRecord, DocType, ScanItem


# —— 3.2 指纹与预筛 ——
def test_streaming_equals_one_shot(tmp_path):
    f = tmp_path / "a.txt"
    payload = b"x" * (3 << 20)  # 3MB > 1MB buf
    f.write_bytes(payload)
    assert file_fingerprint(f) == hashlib.sha256(payload).hexdigest()


def test_maybe_changed_detection(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    st = file_stat(f)
    assert maybe_changed(f, st) is False  # 未变
    assert maybe_changed(f, None) is True  # 无历史视为可能变了
    f.write_text("hello world")
    assert maybe_changed(f, st) is True  # size 变了


def test_text_fingerprint_stable():
    assert text_fingerprint("a") == text_fingerprint("a")
    assert text_fingerprint("a") != text_fingerprint("b")


# —— 3.3 注册表跨会话持久化 ——
def test_registry_persists_across_sessions(tmp_path):
    db = tmp_path / "reg.sqlite"
    rec = DocumentRecord(
        doc_id=make_doc_id("fp1"),
        content_fingerprint="fp1",
        source_path="/x",
        doc_type=DocType.MARKDOWN,
        chunk_ids=("c1", "c2"),
    )
    with DocumentRegistry(db) as reg:
        reg.upsert(rec)
    with DocumentRegistry(db) as reg2:  # 新会话
        got = reg2.get(make_doc_id("fp1"))
    assert got is not None
    assert got.chunk_ids == ("c1", "c2")
    assert got.version == 1


def _rec(fingerprint: str, path: str, ns: str = "local") -> DocumentRecord:
    return DocumentRecord(
        doc_id=make_doc_id(fingerprint, ns),
        content_fingerprint=fingerprint,
        source_path=path,
        doc_type=DocType.MARKDOWN,
    )


def _item(path: str, fingerprint: str) -> ScanItem:
    return ScanItem(
        source_path=path,
        doc_type=DocType.MARKDOWN,
        content_fingerprint=fingerprint,
        content_size=1,
        mtime=1.0,
    )


# —— 3.5 五态 diff ——
def test_diff_new():
    actions = diff([_item("/a.md", "fp_new")], records={})
    assert len(actions) == 1
    assert actions[0].kind is ActionKind.NEW


def test_diff_skip():
    rec = _rec("fp1", "/a.md")
    actions = diff([_item("/a.md", "fp1")], {rec.doc_id: rec})
    assert actions[0].kind is ActionKind.SKIP


def test_diff_update_carries_old_record():
    old = _rec("fp_old", "/a.md")
    actions = diff([_item("/a.md", "fp_new")], {old.doc_id: old})
    upd = next(a for a in actions if a.kind is ActionKind.UPDATE)
    assert upd.old_record is not None
    assert upd.old_record.doc_id == old.doc_id  # 先建新后删旧
    # 旧 doc_id 不再作为独立 DELETE（由 update 负责）
    assert not any(a.kind is ActionKind.DELETE for a in actions)


def test_diff_delete():
    rec = _rec("fp1", "/gone.md")
    actions = diff([], {rec.doc_id: rec})
    assert len(actions) == 1
    assert actions[0].kind is ActionKind.DELETE


def test_diff_move():
    old = _rec("fp1", "/orig.md")
    actions = diff([_item("/moved.md", "fp1")], {old.doc_id: old})
    assert actions[0].kind is ActionKind.MOVE


def test_scan_directory_filters_by_type(tmp_path):
    (tmp_path / "a.md").write_text("# T\n正文")
    (tmp_path / "b.txt").write_text("hi")
    (tmp_path / "c.bin").write_bytes(b"\x00")  # 不支持类型
    items = scan_directory(tmp_path)
    types = {it.doc_type for it in items}
    assert DocType.MARKDOWN in types
    assert DocType.TXT in types
    assert len(items) == 2  # .bin 被忽略


# —— 3.6 两阶段执行 ——


def _act(kind: ActionKind, doc_id: str) -> Action:
    return Action(
        kind=kind,
        doc_id=doc_id,
        source_path=f"/{doc_id}",
        fingerprint="f",
        doc_type=DocType.MARKDOWN,
    )


def test_order_actions_phases():
    ordered = order_actions(
        [
            _act(ActionKind.DELETE, "d"),
            _act(ActionKind.NEW, "n"),
            _act(ActionKind.SKIP, "s"),
        ]
    )
    kinds = [a.kind for a in ordered]
    assert kinds == [ActionKind.NEW, ActionKind.DELETE, ActionKind.SKIP]


def test_execute_failure_isolation():
    def handler(a: Action) -> None:
        if a.doc_id == "boom":
            raise RuntimeError("x")

    res = execute(
        [
            _act(ActionKind.NEW, "ok"),
            _act(ActionKind.NEW, "boom"),
            _act(ActionKind.NEW, "ok2"),
        ],
        handler,
    )
    assert len(res.failed) == 1
    assert res.failed[0][0].doc_id == "boom"
    assert len(res.succeeded) == 2  # 失败被隔离，其余继续
    assert res.counts["failed"] == 1

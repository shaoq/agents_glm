"""目录扫描 + 五态 diff。笔记 §2.5 / §2.6。

``doc_id = content_fingerprint + namespace``。五态：
new（指纹表中无）/ update（同路径内容变）/ delete（源没了）/
move（指纹在但路径变）/ skip（指纹相同）。

边界：复制 vs 移动（同指纹两路径看旧路径是否还在——本实现按内容指纹作身份，
同指纹 = 同 doc_id，多路径指向同一 doc_id 时以注册表记录的路径为基准判定 move）。
"""

from __future__ import annotations

from pathlib import Path

from agents_rag.ingestion.fingerprint import file_fingerprint, file_stat
from agents_rag.models import Action, ActionKind, DocType, DocumentRecord, ScanItem

_EXT_MAP: dict[str, DocType] = {
    ".pdf": DocType.PDF,
    ".md": DocType.MARKDOWN,
    ".markdown": DocType.MARKDOWN,
    ".docx": DocType.DOCX,
    ".xlsx": DocType.XLSX,
    ".pptx": DocType.PPTX,
    ".html": DocType.HTML,
    ".htm": DocType.HTML,
    ".txt": DocType.TXT,
}


def doc_type_for(path: str | Path) -> DocType | None:
    return _EXT_MAP.get(Path(path).suffix.lower())


def make_doc_id(fingerprint: str, namespace: str = "local") -> str:
    """``doc_id = fingerprint:namespace``。多源同名不冲突。"""
    return f"{fingerprint}:{namespace}"


def scan_directory(directory: str | Path, namespace: str = "local") -> list[ScanItem]:
    """递归扫描目录，对每个支持类型的文件计算内容指纹。

    本版为正确性优先，全量计算指纹；预筛（``fingerprint.maybe_changed``）作为
    优化工具就绪，可在批量场景按 stat 缓存跳过未变文件。
    """
    directory = Path(directory)
    items: list[ScanItem] = []
    if not directory.exists():
        return items
    for p in sorted(directory.rglob("*")):
        if not p.is_file():
            continue
        dt = doc_type_for(p)
        if dt is None:
            continue
        st = file_stat(p)
        items.append(
            ScanItem(
                source_path=str(p),
                source_namespace=namespace,
                doc_type=dt,
                content_fingerprint=file_fingerprint(p),
                content_size=st.size,
                mtime=st.mtime,
            )
        )
    return items


def diff(
    scan_items: list[ScanItem], records: dict[str, DocumentRecord]
) -> list[Action]:
    """五态 diff：扫描项 vs 注册表记录 → 动作列表。"""
    scan_by_doc: dict[str, ScanItem] = {
        make_doc_id(it.content_fingerprint, it.source_namespace): it for it in scan_items
    }
    scan_by_path: dict[str, ScanItem] = {it.source_path: it for it in scan_items}
    reg_by_doc = records
    reg_by_path: dict[str, DocumentRecord] = {r.source_path: r for r in records.values()}

    actions: list[Action] = []
    update_old_doc_ids: set[str] = set()

    for doc_id, it in scan_by_doc.items():
        if doc_id in reg_by_doc:
            rec = reg_by_doc[doc_id]
            if rec.source_path == it.source_path:
                actions.append(_action(ActionKind.SKIP, it, doc_id))
            else:
                # 指纹在表但路径变 → 移动（只更路径，不重索引）
                actions.append(_action(ActionKind.MOVE, it, doc_id, rec))
        elif it.source_path in reg_by_path:
            # 新指纹，但该路径曾有旧 doc_id → 更新（内容变）
            old = reg_by_path[it.source_path]
            update_old_doc_ids.add(old.doc_id)
            actions.append(_action(ActionKind.UPDATE, it, doc_id, old))
        else:
            actions.append(_action(ActionKind.NEW, it, doc_id))

    # 注册表中有、扫描结果无 → 删除（排除 update 的旧 doc_id，它们由 update 先建后删）
    for doc_id, rec in reg_by_doc.items():
        if doc_id in scan_by_doc or doc_id in update_old_doc_ids:
            continue
        actions.append(
            Action(
                kind=ActionKind.DELETE,
                doc_id=doc_id,
                source_path=rec.source_path,
                fingerprint=rec.content_fingerprint,
                doc_type=rec.doc_type,
                namespace=rec.source_namespace,
                content_size=rec.content_size,
                old_record=rec,
            )
        )

    return actions


def _action(
    kind: ActionKind, it: ScanItem, doc_id: str, old: DocumentRecord | None = None
) -> Action:
    return Action(
        kind=kind,
        doc_id=doc_id,
        source_path=it.source_path,
        fingerprint=it.content_fingerprint,
        doc_type=it.doc_type,
        namespace=it.source_namespace,
        content_size=it.content_size,
        old_record=old,
    )

"""父块 KV 存储（文档维度）。笔记 §5.5 / §12.1.1。

文档维度组织 ``parents/<doc_id>/<parent_id>.json``：文档删除 = 删整个目录，
目录级重建简单；放弃跨文档去重（企业文档图跨文档复用少）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agents_rag.models import ParentChunk


def _safe(name: str) -> str:
    return name.replace(":", "_").replace("/", "_")


class ParentStore:
    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _doc_dir(self, doc_id: str) -> Path:
        return self._root / _safe(doc_id)

    def _path(self, doc_id: str, parent_id: str) -> Path:
        d = self._doc_dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{_safe(parent_id)}.json"

    def put(self, parent: ParentChunk) -> None:
        self._path(parent.doc_id, parent.id).write_text(
            parent.model_dump_json(), encoding="utf-8"
        )

    def put_many(self, parents: list[ParentChunk]) -> None:
        for p in parents:
            self.put(p)

    def get(self, doc_id: str, parent_id: str) -> ParentChunk | None:
        p = self._doc_dir(doc_id) / f"{_safe(parent_id)}.json"
        if not p.exists():
            return None
        return ParentChunk.model_validate_json(p.read_text(encoding="utf-8"))

    def delete_by_doc(self, doc_id: str) -> None:
        d = self._doc_dir(doc_id)
        if d.exists():
            shutil.rmtree(d)

    def count(self) -> int:
        return sum(1 for d in self._root.iterdir() if d.is_dir() for _ in d.glob("*.json"))

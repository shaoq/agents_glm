from pathlib import Path

import pytest

from agents_memory.models import MemoryRecord, MemoryScope, MemoryType
from agents_memory.storage.vector import ChromaMemoryIndex, IndexModelMismatch


def record(memory_id: str, *, user: str = "u1", type: MemoryType = MemoryType.FACT):
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user),
        type=type,
        content=f"content-{memory_id}",
        importance=7,
        confidence=0.9,
    )


def test_chroma_index_filters_exact_scope_type_and_active(tmp_path: Path) -> None:
    index = ChromaMemoryIndex(tmp_path / "chroma", model="test", dimension=2)
    index.upsert(record("wanted"), [1.0, 0.0])
    index.upsert(record("other-user", user="u2"), [1.0, 0.0])
    index.upsert(record("event", type=MemoryType.EVENT), [1.0, 0.0])

    hits = index.query(
        [1.0, 0.0],
        scope=MemoryScope(user_id="u1"),
        type=MemoryType.FACT,
        top_k=5,
        threshold=0.5,
    )

    assert [hit.memory_id for hit in hits] == ["wanted"]


def test_chroma_upsert_and_delete_are_idempotent(tmp_path: Path) -> None:
    index = ChromaMemoryIndex(tmp_path / "chroma", model="test", dimension=2)
    item = record("m1")

    index.upsert(item, [1.0, 0.0])
    index.upsert(item.model_copy(update={"content": "updated"}), [0.9, 0.1])
    assert index.count() == 1
    index.delete("m1")
    index.delete("m1")
    assert index.count() == 0


def test_chroma_rejects_existing_collection_model_mismatch(tmp_path: Path) -> None:
    ChromaMemoryIndex(tmp_path / "chroma", model="model-a", dimension=2)

    with pytest.raises(IndexModelMismatch):
        ChromaMemoryIndex(tmp_path / "chroma", model="model-b", dimension=2)

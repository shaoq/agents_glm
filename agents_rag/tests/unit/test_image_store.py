"""image_store 测试：存取去重 / 注册表 / 文档级删除 / content_hash 增量。"""

from __future__ import annotations

from agents_rag.indexing.image_store import ImageStore, image_content_hash
from agents_rag.models import ImageRecord


def test_put_get_and_dedup(tmp_path):
    s = ImageStore(tmp_path / "img")
    data = b"\x89PNG\r\n\x1a\n" + b"imagedata"
    img_id = s.put(data, "doc1")
    assert img_id == image_content_hash(data)
    assert s.get("doc1", img_id) == data
    # 同图再存 → 同 id，幂等不报错
    assert s.put(data, "doc1") == img_id
    s.close()


def test_record_upsert_and_find_by_hash(tmp_path):
    s = ImageStore(tmp_path / "img")
    img_id = s.put(b"img", "doc1")
    rec = ImageRecord(
        image_id=img_id,
        doc_id="doc1",
        source_path=str(s.path_of("doc1", img_id)),
        description="一张图表",
        content_hash=img_id,
    )
    s.upsert_record(rec)
    assert s.get_record(img_id).description == "一张图表"
    assert s.find_by_hash(img_id).image_id == img_id  # 增量复用句柄
    s.close()


def test_delete_by_doc_clears_dir_and_records(tmp_path):
    s = ImageStore(tmp_path / "img")
    img_id = s.put(b"img", "doc1")
    s.upsert_record(
        ImageRecord(
            image_id=img_id, doc_id="doc1", source_path="x",
            description="d", content_hash=img_id,
        )
    )
    s.delete_by_doc("doc1")
    assert s.get("doc1", img_id) is None
    assert s.list_by_doc("doc1") == []
    s.close()

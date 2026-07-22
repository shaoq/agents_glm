"""image_store 测试：存取去重 / 格式检测 / 注册表 / 文档级删除 / content_hash 增量。"""

from __future__ import annotations

from agents_rag.indexing.image_store import ImageStore, detect_format, image_content_hash
from agents_rag.models import ImageRecord


def test_detect_format_magic_bytes():
    assert detect_format(b"\x89PNG\r\n\x1a\n...") == "png"
    assert detect_format(b"\xff\xd8\xff...") == "jpeg"
    assert detect_format(b"GIF89a...") == "gif"
    assert detect_format(b"RIFF\x00\x00\x00\x00WEBP...") == "webp"
    assert detect_format(b"unknown") == "png"  # 默认


def test_put_get_and_dedup(tmp_path):
    s = ImageStore(tmp_path / "img")
    data = b"\x89PNG\r\n\x1a\n" + b"imagedata"
    img_ref = s.put(data, "doc1", "png")
    assert img_ref == f"{image_content_hash(data)}.png"  # 文件名含扩展
    assert s.get("doc1", img_ref) == data
    assert s.put(data, "doc1", "png") == img_ref  # 幂等
    s.close()


def test_record_upsert_and_find_by_hash(tmp_path):
    s = ImageStore(tmp_path / "img")
    data = b"\x89PNG imagedata"
    img_ref = s.put(data, "doc1", "png")
    img_id = image_content_hash(data)
    rec = ImageRecord(
        image_id=img_id,
        doc_id="doc1",
        source_path=str(s.path_of("doc1", img_ref)),
        description="一张图表",
        format="png",
        content_hash=img_id,
    )
    s.upsert_record(rec)
    assert s.get_record(img_id).description == "一张图表"
    assert s.get_record(img_id).format == "png"
    assert s.find_by_hash(img_id).image_id == img_id  # 增量复用句柄
    s.close()


def test_delete_by_doc_clears_dir_and_records(tmp_path):
    s = ImageStore(tmp_path / "img")
    data = b"\x89PNG img"
    img_ref = s.put(data, "doc1", "png")
    img_id = image_content_hash(data)
    s.upsert_record(
        ImageRecord(
            image_id=img_id, doc_id="doc1", source_path="x",
            description="d", format="png", content_hash=img_id,
        )
    )
    s.delete_by_doc("doc1")
    assert s.get("doc1", img_ref) is None
    assert s.list_by_doc("doc1") == []
    s.close()

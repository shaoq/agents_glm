"""config 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents_rag.config import Settings


def test_derived_paths():
    s = Settings(storage_dir="/tmp/s_x", data_dir="/tmp/d_x")
    assert s.chroma_dir == Path("/tmp/s_x/chroma")
    assert s.bm25_path == Path("/tmp/s_x/bm25.pkl")
    assert s.parents_dir == Path("/tmp/s_x/parents")
    assert s.embedding_cache_path == Path("/tmp/s_x/embedding_cache.sqlite")
    assert s.registry_path == Path("/tmp/s_x/registry.sqlite")
    assert s.raw_dir == Path("/tmp/d_x/raw")


def test_require_api_key_failfast_when_empty():
    s = Settings(llm_api_key="")
    with pytest.raises(RuntimeError):
        s.require_api_key()


def test_require_api_key_returns_when_set():
    s = Settings(llm_api_key="sk-test")
    assert s.require_api_key() == "sk-test"


def test_ensure_storage_dirs(tmp_path):
    s = Settings(storage_dir=tmp_path / "s", data_dir=tmp_path / "d")
    s.ensure_storage_dirs()
    assert (tmp_path / "s" / "chroma").is_dir()
    assert (tmp_path / "s" / "parents").is_dir()
    assert (tmp_path / "d" / "raw").is_dir()

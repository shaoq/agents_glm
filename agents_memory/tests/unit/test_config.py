from pathlib import Path

import pytest

from agents_memory.config import Settings


def test_settings_resolve_storage_under_project(tmp_path: Path) -> None:
    settings = Settings(memory_storage_dir=tmp_path)

    assert settings.sqlite_path == tmp_path / "memory.sqlite"
    assert settings.chroma_path == tmp_path / "chroma"


def test_write_configuration_requires_api_key() -> None:
    settings = Settings(llm_api_key=None)

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        settings.validate_write()


def test_lookup_threshold_must_be_probability() -> None:
    with pytest.raises(ValueError):
        Settings(memory_lookup_threshold=1.1)


def test_pending_ttls_are_positive_and_configurable() -> None:
    settings = Settings(
        pending_high_ttl_days=60,
        pending_normal_ttl_days=10,
        pending_low_ttl_days=2,
    )

    assert settings.pending_high_ttl_days == 60
    with pytest.raises(ValueError):
        Settings(pending_low_ttl_days=0)

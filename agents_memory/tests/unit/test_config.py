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


def test_recall_defaults_match_design() -> None:
    settings = Settings()

    assert settings.memory_recall_model == "glm-4.7-flash"
    assert settings.recall_session_quota == 10
    assert settings.recall_agent_history_quota == 10
    assert settings.recall_user_shared_quota == 10
    assert settings.recall_global_candidate_limit == 30
    assert settings.recall_llm_review_top_n == 10
    assert settings.recall_default_max_evidence_items == 10
    assert settings.recall_default_max_context_tokens == 2000


def test_recall_evidence_budget_has_hard_limits() -> None:
    # Aligned with RecallRequest ClassVar ceilings (50 items / 8000 tokens).
    with pytest.raises(ValueError):
        Settings(recall_default_max_evidence_items=51)
    with pytest.raises(ValueError):
        Settings(recall_default_max_context_tokens=8001)


def test_recall_quotas_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(recall_session_quota=0)
    with pytest.raises(ValueError):
        Settings(recall_agent_history_quota=0)
    with pytest.raises(ValueError):
        Settings(recall_user_shared_quota=0)
    with pytest.raises(ValueError):
        Settings(recall_global_candidate_limit=0)


def test_validate_recall_requires_api_key() -> None:
    settings = Settings(llm_api_key=None)

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        settings.validate_recall()


def test_validate_recall_passes_with_api_key() -> None:
    settings = Settings(llm_api_key="sk-test")

    settings.validate_recall()  # no raise


def test_recall_validation_is_lazy_at_construction() -> None:
    # Storage-only and maintenance commands must still construct Settings
    # without LLM_API_KEY; recall validation only runs on explicit demand.
    settings = Settings(llm_api_key=None)

    assert settings.llm_api_key is None

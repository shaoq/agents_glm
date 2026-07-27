"""Application settings.

Loaded from environment / ``.env`` via ``pydantic-settings``. The key set here
MUST stay aligned with ``.env.example`` (project rule: ``.env`` and
``.env.example`` share the same keys).

System limits (:class:`agents_orchestration.domain.policy.SystemLimits`) and run
policy defaults are wired into these settings in Section 12; this module holds
the baseline storage, web and model-profile configuration.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Baseline runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ORCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sqlite_path: Path = Field(default=Path("storage/runtime.sqlite"))
    artifact_dir: Path = Field(default=Path("storage/artifacts"))

    web_enabled: bool = Field(default=False)
    web_allowed_domains: tuple[str, ...] = Field(default_factory=tuple)

    llm_api_key: str = Field(default="")
    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/")
    model_normalizer: str = Field(default="glm-4.7-flash")
    model_planner: str = Field(default="glm-4.7-flash")
    model_reviewer: str = Field(default="glm-4.7-flash")


def load_settings() -> Settings:
    """Load settings from the environment / ``.env``."""

    return Settings()

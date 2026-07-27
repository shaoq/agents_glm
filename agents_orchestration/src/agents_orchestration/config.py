"""Application settings (tasks 12.1 / 12.2 / 12.3 / 12.4).

The key set here MUST stay aligned with ``.env.example`` (project rule). Settings
carry storage paths, model profiles, the system maximums and run-policy defaults.
``SystemLimits`` / ``RunPolicy`` are derived deterministically from these values;
a RunPolicy may only remain within or tighten the system limits (task 12.2).
Secrets are read here at the boundary and never propagated into prompts, state,
events, checkpoints, artifacts or logs (task 12.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from agents_orchestration.domain.policy import RunPolicy, SystemLimits


class Settings(BaseSettings):
    """Baseline runtime configuration (env prefix ``ORCH_``)."""

    model_config = SettingsConfigDict(
        env_prefix="ORCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---
    sqlite_path: Path = Field(default=Path("storage/runtime.sqlite"))
    artifact_dir: Path = Field(default=Path("storage/artifacts"))

    # --- Web Research (disabled by default; opt-in per Run Policy, task 12.3) ---
    web_enabled: bool = Field(default=False)
    web_allowed_domains: tuple[str, ...] = Field(default_factory=tuple)

    # --- Model profiles (OpenAI-compatible; secrets read only at Adapter boundary) ---
    llm_api_key: str = Field(default="")
    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/")
    model_normalizer: str = Field(default="glm-4.7-flash")
    model_planner: str = Field(default="glm-4.7-flash")
    model_reviewer: str = Field(default="glm-4.7-flash")

    # --- System maximums (task 12.1) ---
    max_tasks: int = Field(default=32, ge=1)
    max_plan_depth: int = Field(default=4, ge=1)
    max_concurrency: int = Field(default=4, ge=1)
    max_attempts_per_task: int = Field(default=3, ge=1)
    max_replans: int = Field(default=2, ge=0)
    max_report_revisions: int = Field(default=2, ge=0)
    run_deadline_seconds: int = Field(default=1800, gt=0)

    def build_limits(self) -> SystemLimits:
        return SystemLimits(
            max_tasks=self.max_tasks,
            max_plan_depth=self.max_plan_depth,
            max_concurrency=self.max_concurrency,
            max_attempts_per_task=self.max_attempts_per_task,
            max_replans=self.max_replans,
            max_report_revisions=self.max_report_revisions,
            default_run_deadline_seconds=self.run_deadline_seconds,
        )

    def build_run_policy(self, **tighten: object) -> RunPolicy:
        """Build the default RunPolicy from system limits (task 12.2).

        ``tighten`` may only narrow a field; anything that would exceed the
        system limits raises, so a Run Policy can never expand permissions.
        """

        policy = RunPolicy.from_limits(self.build_limits(), **tighten)
        if not policy.within(self.build_limits()):
            raise ValueError("RunPolicy exceeds system limits")
        return policy

    def redacted(self) -> dict:
        """A secret-safe view for diagnostics / ``capability doctor`` (12.4/12.7)."""

        return {
            "sqlite_path": str(self.sqlite_path),
            "artifact_dir": str(self.artifact_dir),
            "web_enabled": self.web_enabled,
            "web_allowed_domains": list(self.web_allowed_domains),
            "llm_base_url": self.llm_base_url,
            "model_normalizer": self.model_normalizer,
            "model_planner": self.model_planner,
            "model_reviewer": self.model_reviewer,
            "llm_api_key": "***" if self.llm_api_key else "",
        }


@dataclass(frozen=True)
class ModelProfileConfig:
    name: str
    base_url: str
    api_key: str


def load_settings() -> Settings:
    """Load settings from the environment / ``.env``."""

    return Settings()

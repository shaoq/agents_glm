from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_key: str | None = None
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    memory_extract_model: str = "glm-4.7-flash"
    memory_relation_model: str = "glm-4.7-flash"
    embedding_model: str = "embedding-3"
    embedding_dim: int = Field(default=2048, gt=0)
    embedding_max_batch: int = Field(default=64, gt=0)
    embedding_max_concurrency: int = Field(default=8, gt=0)
    memory_lookup_top_k: int = Field(default=5, gt=0)
    memory_lookup_threshold: float = Field(default=0.72, ge=0, le=1)
    pending_high_ttl_days: int = Field(default=30, gt=0)
    pending_normal_ttl_days: int = Field(default=7, gt=0)
    pending_low_ttl_days: int = Field(default=1, gt=0)
    memory_storage_dir: Path = Path("storage")

    @property
    def sqlite_path(self) -> Path:
        return self.memory_storage_dir / "memory.sqlite"

    @property
    def chroma_path(self) -> Path:
        return self.memory_storage_dir / "chroma"

    def validate_write(self) -> None:
        if not self.llm_api_key:
            raise ValueError("LLM_API_KEY is required for write operations")

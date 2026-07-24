"""配置：pydantic-settings 从 .env 与环境变量加载。

密钥不在这里 fail-fast（避免无密钥场景如单元测试无法 import）；
需要密钥的组件调用 ``settings.require_api_key()`` 时才校验。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行配置。字段名与 .env 变量一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM 平台（OpenAI 兼容端点）：当前指向智谱 BigModel
    llm_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"

    # Embedding
    embedding_model: str = "embedding-3"
    embedding_dim: int = 2048
    embedding_max_batch: int = 64
    embedding_max_concurrency: int = 8

    # 视觉（图片描述生成，OpenAI 兼容视觉模型，默认智谱 glm-4.5v）
    vision_model: str = "glm-4.5v"

    # 查询管线
    llm_model: str = "glm-5.2"
    rerank_model: str = "rerank-2"
    vector_top_k: int = 20
    bm25_top_k: int = 20
    rerank_top_n: int = 6
    llm_max_context_tokens: int = 6000

    # Contextual Retrieval（chunk 上下文前缀，便宜 LLM 生成）
    contextualization_enabled: bool = False
    contextualization_model: str = "GLM-4.7-Flash"
    contextualization_max_tokens: int = 150
    contextualization_max_concurrency: int = 8

    # Faithfulness 二次校验（LLM judge，默认关）
    faithfulness_enabled: bool = False
    faithfulness_model: str = "GLM-4.7-Flash"

    # 置信度聚合拒答（多信号加权，默认关）
    confidence_enabled: bool = False
    confidence_threshold: float = 0.5
    confidence_weight_rerank: float = 0.3
    confidence_weight_citation: float = 0.3
    confidence_weight_faithfulness: float = 0.4

    # 查询改写（Flash 把口语/模糊 query 改写为检索友好，默认关）
    query_rewrite_enabled: bool = False
    query_rewrite_model: str = "GLM-4.7-Flash"

    # 分块
    chunk_size: int = 400
    chunk_overlap: int = 64
    parent_max_size: int = 1800

    # 路径（相对 cwd）
    data_dir: Path = Path("./data")
    storage_dir: Path = Path("./storage")

    # —— 派生存储路径 ——
    @property
    def chroma_dir(self) -> Path:
        return self.storage_dir / "chroma"

    @property
    def bm25_path(self) -> Path:
        return self.storage_dir / "bm25.pkl"

    @property
    def parents_dir(self) -> Path:
        return self.storage_dir / "parents"

    @property
    def images_dir(self) -> Path:
        return self.storage_dir / "images"

    @property
    def embedding_cache_path(self) -> Path:
        return self.storage_dir / "embedding_cache.sqlite"

    @property
    def context_cache_path(self) -> Path:
        return self.storage_dir / "context_cache.sqlite"

    @property
    def registry_path(self) -> Path:
        return self.storage_dir / "registry.sqlite"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    def require_api_key(self) -> str:
        """需要密钥的组件调用；缺失 fail-fast。"""
        if not self.llm_api_key:
            raise RuntimeError(
                "LLM_API_KEY 未配置：请在项目根的 .env 或环境变量中设置。"
            )
        return self.llm_api_key

    def ensure_storage_dirs(self) -> None:
        """创建存储相关目录（幂等）。"""
        for p in (
            self.storage_dir,
            self.chroma_dir,
            self.parents_dir,
            self.images_dir,
            self.data_dir,
            self.raw_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """读取配置（每次调用重新读取，便于测试注入环境变量）。"""
    return Settings()


# 模块级单例：多数场景直接 import settings 使用。
settings = get_settings()

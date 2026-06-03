from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    mock_external_tools: bool = True
    auth_enabled: bool = False
    admin_email: str = "1173817292@qq.com"
    admin_password: str | None = None
    jwt_secret_key: str = "change-me-local-dev-secret"
    auth_token_ttl_minutes: int = 60 * 24
    task_queue_backend: str = "thread"
    task_queue_redis_queue_name: str = "ai_visual_agent:jobs"
    background_worker_concurrency: int = 4
    background_job_recovery_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_backend: str = "memory"
    rate_limit_default_per_minute: int = 120
    rate_limit_agent_per_minute: int = 30
    rate_limit_upload_per_minute: int = 60
    rate_limit_image_generation_per_minute: int = 6
    rate_limit_image_generation_global_per_minute: int = 20
    image_generation_max_concurrent: int = 1
    image_generation_acquire_timeout_seconds: float = 20.0
    project_store_backend: str = "sqlite"
    graph_checkpoint_backend: str = "memory"
    document_parser_backend: str = "local"
    auto_analyze_images: bool = True
    auto_analyze_max_images: int = 12
    ocr_backend: str = "mock"
    ocr_language: str = "ch"
    mock_ocr_text: str = ""
    segmentation_backend: str = "mock"
    sam2_checkpoint: str | None = None
    sam2_model_cfg: str | None = None
    multimodal_backend: str = "mock"
    multimodal_api_key: str | None = None
    multimodal_base_url: str | None = None
    multimodal_model: str = "gemini-2.5-flash"
    llm_backend: str = "mock"
    llm_temperature: float = 0.2
    llm_retry_attempts: int = 3
    llm_retry_backoff_seconds: float = 0.3
    llm_request_timeout: float = 120.0

    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_strategy: str = "deepseek-v4-pro"
    deepseek_model_fast: str = "deepseek-v4-flash"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_image_model: str = "gpt-image-2"
    image_generation_backend: str = "auto"
    image_generation_real_names: str = "front"
    image_generation_quality: str = "low"
    image_generation_timeout: float = 420.0

    gemini_api_key: str | None = None
    gemini_model_video: str = "gemini-2.5-flash"

    llama_cloud_api_key: str | None = None
    llama_parse_tier: str = "fast"
    llama_parse_version: str = "latest"
    llama_parse_timeout: float = 120.0

    database_url: str = "postgresql+psycopg://vision_agent:vision_agent@localhost:5432/vision_agent"
    local_database_url: str = "sqlite:///data/vision_agent.db"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "ai_visual_agent_memory"
    memory_embedding_dim: int = 128

    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "vision-agent"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    storage_backend: str = "local"

    storage_dir: Path = Field(default=Path("data"))
    max_revision_rounds: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()

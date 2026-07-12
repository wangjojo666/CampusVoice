from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded exclusively from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_prefix="CAMPUSVOICE_",
        extra="ignore",
    )

    app_name: str = "CampusVoice API"
    app_version: str = "0.1.0"
    env: str = "development"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    database_url: str = "sqlite+aiosqlite:///./data/campusvoice.db"
    database_auto_create: bool = True
    single_user_id: str = "user_demo"
    action_ttl_minutes: int = 30
    undo_ttl_minutes: int = 1440
    asr_provider: str = "disabled"
    asr_device: str = "cpu"
    asr_model: str = "paraformer-zh-streaming"
    asr_vad_model: str = "fsmn-vad"
    asr_punc_model: str = "ct-punc"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    knowledge_retriever: Literal["embedding", "lexical"] = "embedding"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"


@lru_cache
def get_settings() -> Settings:
    return Settings()

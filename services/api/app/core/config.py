from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASYMMETRIC_JWT_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)


class Settings(BaseSettings):
    """Runtime settings loaded exclusively from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_prefix="CAMPUSVOICE_",
        extra="ignore",
    )

    app_name: str = "CampusVoice API"
    app_version: str = "0.2.0"
    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    database_url: str = "sqlite+aiosqlite:///./data/campusvoice.db"
    database_auto_create: bool = False
    auth_mode: Literal["demo", "jwt"] = "demo"
    demo_user_id: str = "user_demo"
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_jwks_url: str | None = None
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    websocket_ticket_ttl_seconds: int = Field(default=30, ge=10, le=120)
    confirmation_secret: SecretStr | None = None
    confirmation_challenge_ttl_seconds: int = Field(default=120, ge=30, le=600)
    action_ttl_minutes: int = 30
    undo_ttl_minutes: int = 1440
    asr_provider: Literal["disabled", "funasr", "whisper"] = "disabled"
    asr_device: str = "cpu"
    asr_model: str = "paraformer-zh-streaming"
    asr_vad_model: str = "fsmn-vad"
    asr_punc_model: str = "ct-punc"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    knowledge_retriever: Literal["embedding", "lexical"] = "lexical"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    asr_max_frame_bytes: int = Field(default=131_072, ge=1_920, le=1_048_576)
    asr_max_control_message_bytes: int = Field(default=32_768, ge=256, le=1_048_576)
    asr_idle_timeout_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    asr_max_session_seconds: float = Field(default=600.0, ge=10.0, le=3_600.0)
    asr_max_audio_seconds: float = Field(default=300.0, ge=5.0, le=3_600.0)
    asr_max_connections_per_user: int = Field(default=1, ge=1, le=10)
    store_raw_audio: bool = False
    transcription_retention_days: int = Field(default=30, ge=1, le=3_650)
    correction_retention_days: int = Field(default=30, ge=1, le=3_650)
    conversation_retention_days: int = Field(default=7, ge=1, le=3_650)
    pending_action_retention_days: int = Field(default=30, ge=1, le=3_650)
    audit_retention_days: int = Field(default=180, ge=1, le=3_650)
    privacy_deletion_challenge_ttl_seconds: int = Field(default=120, ge=30, le=600)

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if self.auth_mode == "demo" and self.env not in {"development", "test"}:
            raise ValueError("demo authentication is allowed only in development or test")
        if self.auth_mode == "jwt":
            missing = [
                name
                for name, value in (
                    ("jwt_issuer", self.jwt_issuer),
                    ("jwt_audience", self.jwt_audience),
                    ("jwt_jwks_url", self.jwt_jwks_url),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"JWT authentication requires: {', '.join(missing)}")
            if not self.jwt_algorithms or any(
                value not in _ASYMMETRIC_JWT_ALGORITHMS for value in self.jwt_algorithms
            ):
                supported = ", ".join(sorted(_ASYMMETRIC_JWT_ALGORITHMS))
                raise ValueError(
                    "JWT authentication requires a supported asymmetric signing algorithm: "
                    + supported
                )
        if self.env == "production":
            if self.auth_mode != "jwt":
                raise ValueError("production requires CAMPUSVOICE_AUTH_MODE=jwt")
            secret = self.confirmation_secret.get_secret_value() if self.confirmation_secret else ""
            if len(secret) < 32:
                raise ValueError(
                    "production requires CAMPUSVOICE_CONFIRMATION_SECRET "
                    "with at least 32 characters"
                )
            if self.database_auto_create:
                raise ValueError("production requires Alembic migrations; auto-create is forbidden")
        if self.store_raw_audio:
            raise ValueError("raw audio persistence is not implemented and must remain disabled")
        llm_values = (self.llm_base_url, self.llm_model)
        if any(llm_values) and not all(llm_values):
            raise ValueError("LLM base URL and model must be configured together")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

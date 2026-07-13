from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

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


def _require_production_https_url(name: str, value: str, *, issuer: bool = False) -> None:
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError as exc:
        raise ValueError(f"production requires a valid HTTPS URL for {name}") from exc
    invalid = (
        value != value.strip()
        or "\r" in value
        or "\n" in value
        or parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or bool(parts.fragment and (issuer or name == "oidc_redirect_uri"))
        or bool(issuer and parts.query)
    )
    if invalid:
        raise ValueError(f"production requires a valid HTTPS URL for {name}")


class Settings(BaseSettings):
    """Runtime settings loaded exclusively from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_prefix="CAMPUSVOICE_",
        extra="ignore",
    )

    app_name: str = "CampusVoice API"
    app_version: str = "0.3.0"
    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    database_url: str = "sqlite+aiosqlite:///./data/campusvoice.db"
    database_auto_create: bool = False
    auth_mode: Literal["demo", "jwt", "oidc"] = "demo"
    demo_user_id: str = "user_demo"
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_jwks_url: str | None = None
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str | None = None
    oidc_post_login_redirect_uri: str | None = None
    oidc_post_logout_redirect_uri: str | None = None
    oidc_scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    oidc_id_token_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    oidc_login_ttl_seconds: int = Field(default=300, ge=60, le=900)
    oidc_session_ttl_seconds: int = Field(default=28_800, ge=300, le=86_400)
    oidc_http_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    oidc_session_cookie_name: str = Field(
        default="campusvoice_session", pattern=r"^[A-Za-z0-9_-]{1,64}$"
    )
    oidc_flow_cookie_name: str = Field(
        default="campusvoice_oidc_flow", pattern=r"^[A-Za-z0-9_-]{1,64}$"
    )
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
    asr_quota_backend: Literal["local", "redis"] = "local"
    asr_worker_count: int = Field(default=1, ge=1, le=128)
    asr_redis_url: SecretStr | None = None
    asr_redis_key_prefix: str = Field(default="campusvoice:asr:quota", min_length=1, max_length=100)
    asr_quota_lease_grace_seconds: int = Field(default=30, ge=5, le=300)
    store_raw_audio: bool = False
    transcription_retention_days: int = Field(default=30, ge=1, le=3_650)
    correction_retention_days: int = Field(default=30, ge=1, le=3_650)
    conversation_retention_days: int = Field(default=7, ge=1, le=3_650)
    pending_action_retention_days: int = Field(default=30, ge=1, le=3_650)
    audit_retention_days: int = Field(default=180, ge=1, le=3_650)
    privacy_deletion_challenge_ttl_seconds: int = Field(default=120, ge=30, le=600)
    retention_scheduler_max_retries: int = Field(default=3, ge=0, le=10)
    retention_scheduler_retry_base_seconds: float = Field(default=5.0, ge=0.1, le=300.0)

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
        if "*" in self.cors_origins:
            raise ValueError(
                "Credentialed browser requests forbid wildcard CORS origins; "
                "configure exact origins"
            )
        if self.auth_mode == "oidc":
            missing = [
                name
                for name, value in (
                    ("oidc_issuer", self.oidc_issuer),
                    ("oidc_client_id", self.oidc_client_id),
                    ("oidc_redirect_uri", self.oidc_redirect_uri),
                    ("oidc_post_login_redirect_uri", self.oidc_post_login_redirect_uri),
                    ("oidc_post_logout_redirect_uri", self.oidc_post_logout_redirect_uri),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"OIDC authentication requires: {', '.join(missing)}")
            if "openid" not in self.oidc_scopes:
                raise ValueError("OIDC scopes must include openid")
            if not self.oidc_id_token_algorithms or any(
                value not in _ASYMMETRIC_JWT_ALGORITHMS for value in self.oidc_id_token_algorithms
            ):
                raise ValueError("OIDC ID tokens require a supported asymmetric signing algorithm")
            if self.env == "production":
                for name, value in (
                    ("oidc_issuer", self.oidc_issuer),
                    ("oidc_redirect_uri", self.oidc_redirect_uri),
                    ("oidc_post_login_redirect_uri", self.oidc_post_login_redirect_uri),
                    ("oidc_post_logout_redirect_uri", self.oidc_post_logout_redirect_uri),
                ):
                    if value:
                        _require_production_https_url(
                            name,
                            value,
                            issuer=name == "oidc_issuer",
                        )
        redis_url = self.asr_redis_url.get_secret_value() if self.asr_redis_url else ""
        if self.asr_quota_backend == "redis" and not redis_url:
            raise ValueError("Redis ASR quota mode requires CAMPUSVOICE_ASR_REDIS_URL")
        if self.asr_worker_count > 1 and self.asr_quota_backend != "redis":
            raise ValueError("multiple ASR workers require CAMPUSVOICE_ASR_QUOTA_BACKEND=redis")
        confirmation_secret = (
            self.confirmation_secret.get_secret_value() if self.confirmation_secret else ""
        )
        if self.asr_worker_count > 1 and len(confirmation_secret) < 32:
            raise ValueError(
                "multiple workers require a shared CAMPUSVOICE_CONFIRMATION_SECRET "
                "with at least 32 characters"
            )
        if self.env == "production":
            if self.auth_mode == "demo":
                raise ValueError("production requires CAMPUSVOICE_AUTH_MODE=jwt or oidc")
            if len(confirmation_secret) < 32:
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

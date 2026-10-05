"""Application settings loaded via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration — all values from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_BASE_URL: str = "http://localhost:8000"
    WEB_BASE_URL: str = "http://localhost:3000"

    # ── Datastores ─────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://assistant:assistant@localhost:5432/assistant"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── OIDC ───────────────────────────────────────────────────────
    OIDC_ISSUER: str = ""
    OIDC_AUDIENCE: str = ""
    OIDC_JWKS_URL: str = ""

    # ── Atlassian (Confluence / Jira) ──────────────────────────────
    ATLASSIAN_CLIENT_ID: str = ""
    ATLASSIAN_CLIENT_SECRET: str = ""
    ATLASSIAN_REDIRECT_URI: str = ""

    # ── Slack ──────────────────────────────────────────────────────
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_REDIRECT_URI: str = ""

    # ── Google Drive ───────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""

    # ── Models ─────────────────────────────────────────────────────
    MODEL_PROVIDER: str = "openai"
    MODEL_API_KEY: str = ""
    GENERATION_MODEL: str = "gpt-4o-mini"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    RERANK_MODEL: str = "gpt-4o-mini"

    # ── Object Store (S3-compatible) ───────────────────────────────
    OBJECT_STORE_ENDPOINT: str = "http://localhost:9000"
    OBJECT_STORE_BUCKET: str = "audit-checkpoints"
    OBJECT_STORE_ACCESS_KEY: str = ""
    OBJECT_STORE_SECRET_KEY: str = ""

    # ── KMS ────────────────────────────────────────────────────────
    KMS_KEY_ID: str = "dev-local-key"

    # ── Query deadlines / intervals ────────────────────────────────
    QUERY_AUTH_DEADLINE_MS: int = 1500
    QUERY_GENERATION_DEADLINE_MS: int = 6000
    RECONCILIATION_INTERVAL_MINUTES: int = 15

    # ── Encryption ──────────────────────────────────────────────────
    ENCRYPTION_MASTER_KEY: str = Field(
        default="dev-only-do-not-use-in-production-32bytes!!",
        description="Local dev Fernet key. Production uses managed KMS.",
    )

    # ── Embedding dimensions (per model) ──────────────────────────
    EMBEDDING_DIMENSION: int = 1536

    # ── Retrieval defaults ─────────────────────────────────────────
    CANDIDATE_TOP_K: int = 50
    LIVE_AUTH_TOP_OBJECTS: int = 30
    FINAL_EVIDENCE_COUNT: int = 10
    RRF_K: int = 60
    SOURCE_DIVERSITY_MAX_PCT: float = 0.5

    # ── Provider concurrency limits ────────────────────────────────
    PROVIDER_CONCURRENCY_LIMIT: int = 8

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()  # type: ignore[call-arg]

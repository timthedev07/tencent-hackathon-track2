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
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    SQL_ECHO: bool = False

    # ── Datastores ─────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://assistant:assistant@localhost:5432/assistant"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── OIDC ───────────────────────────────────────────────────────
    OIDC_ISSUER: str = ""
    OIDC_AUDIENCE: str = ""
    OIDC_JWKS_URL: str = ""
    OIDC_TENANT_CLAIM: str = "org_id"

    # ── Atlassian (Confluence / Jira) ──────────────────────────────
    ATLASSIAN_CLIENT_ID: str = ""
    ATLASSIAN_CLIENT_SECRET: str = ""
    ATLASSIAN_WEBHOOK_SECRET: str = ""
    ATLASSIAN_REDIRECT_URI: str = ""

    # ── Slack ──────────────────────────────────────────────────────
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_REDIRECT_URI: str = ""
    GOOGLE_WEBHOOK_TOKEN: str = ""

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
    AUDIT_CHECKPOINT_SIGNING_KEY: str = ""

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

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    def validate_for_startup(self) -> None:
        """Fail closed when production-critical settings are missing."""
        if not self.is_production:
            return
        if "*" in self.cors_origins:
            raise RuntimeError("CORS_ALLOWED_ORIGINS must not contain '*' in production")
        required = {
            "OIDC_ISSUER": self.OIDC_ISSUER,
            "OIDC_AUDIENCE": self.OIDC_AUDIENCE,
            "OIDC_JWKS_URL": self.OIDC_JWKS_URL,
            "OIDC_TENANT_CLAIM": self.OIDC_TENANT_CLAIM,
            "MODEL_API_KEY": self.MODEL_API_KEY,
            "ATLASSIAN_CLIENT_ID": self.ATLASSIAN_CLIENT_ID,
            "ATLASSIAN_CLIENT_SECRET": self.ATLASSIAN_CLIENT_SECRET,
            "ATLASSIAN_WEBHOOK_SECRET": self.ATLASSIAN_WEBHOOK_SECRET,
            "ATLASSIAN_REDIRECT_URI": self.ATLASSIAN_REDIRECT_URI,
            "SLACK_CLIENT_ID": self.SLACK_CLIENT_ID,
            "SLACK_CLIENT_SECRET": self.SLACK_CLIENT_SECRET,
            "SLACK_SIGNING_SECRET": self.SLACK_SIGNING_SECRET,
            "SLACK_REDIRECT_URI": self.SLACK_REDIRECT_URI,
            "GOOGLE_WEBHOOK_TOKEN": self.GOOGLE_WEBHOOK_TOKEN,
            "GOOGLE_CLIENT_ID": self.GOOGLE_CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": self.GOOGLE_CLIENT_SECRET,
            "GOOGLE_REDIRECT_URI": self.GOOGLE_REDIRECT_URI,
            "OBJECT_STORE_ACCESS_KEY": self.OBJECT_STORE_ACCESS_KEY,
            "OBJECT_STORE_SECRET_KEY": self.OBJECT_STORE_SECRET_KEY,
            "AUDIT_CHECKPOINT_SIGNING_KEY": self.AUDIT_CHECKPOINT_SIGNING_KEY,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing production settings: {', '.join(sorted(missing))}")
        if self.ENCRYPTION_MASTER_KEY == "dev-only-do-not-use-in-production-32bytes!!":
            raise RuntimeError("ENCRYPTION_MASTER_KEY must be changed in production")
        if self.AUDIT_CHECKPOINT_SIGNING_KEY == "dev-audit-checkpoint-key":
            raise RuntimeError("AUDIT_CHECKPOINT_SIGNING_KEY must be changed in production")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()  # type: ignore[call-arg]

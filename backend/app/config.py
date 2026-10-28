from functools import lru_cache
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Company Brain"
    environment: Literal["development", "staging", "production", "testing"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # PostgreSQL Database Configuration
    postgres_user: str = "company_brain"
    postgres_password: str = "company_brain_secret"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "company_brain"
    database_url: str | None = None
    test_database_url: str | None = None

    # Authentication & Session Token Configuration
    jwt_secret_key: str = "dev-secret-key-change-in-production-must-be-32-chars-long"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "company-brain-auth"
    jwt_audience: str = "company-brain-api"
    jwt_expire_seconds: int = 3600
    enable_dev_auth: bool = True

    # Cookie & Browser Session Configuration
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_session_name: str = "cb_session_token"
    cookie_oauth_state_name: str = "cb_oauth_state"

    # Google Workspace OIDC Configuration
    google_client_id: str = "google-dev-client-id.apps.googleusercontent.com"
    google_client_secret: str = "google-dev-client-secret"
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    google_allowed_domains: list[str] = []

    # Deployment Architecture
    uvicorn_workers: int = 1

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Validate critical security settings in staging and production."""
        if self.uvicorn_workers > 1:
            raise ValueError(
                "Multi-worker deployment (uvicorn_workers > 1) is currently not supported with "
                "in-memory ephemeral OAuth state storage. Run single-worker (uvicorn_workers=1) "
                "or configure a shared distributed store (e.g. Redis)."
            )

        if self.environment in ("staging", "production"):
            self.enable_dev_auth = False
            if "dev-secret-key" in self.jwt_secret_key or len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "Insecure jwt_secret_key in staging/production. "
                    "Must be set via environment variable and be at least 32 characters long."
                )
            if not self.google_client_id or "dev-client" in self.google_client_id:
                raise ValueError(
                    "Missing or insecure google_client_id in staging/production."
                )
            if not self.google_client_secret or "dev-client" in self.google_client_secret:
                raise ValueError(
                    "Missing or insecure google_client_secret in staging/production."
                )
            self.cookie_secure = True
        return self

    @property
    def async_database_url(self) -> str:
        """Return the asyncpg-compatible SQLAlchemy database connection URL."""
        if self.database_url:
            url = self.database_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_test_database_url(self) -> str:
        """Return the asyncpg-compatible SQLAlchemy test database connection URL."""
        if self.test_database_url:
            url = self.test_database_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}_test"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()

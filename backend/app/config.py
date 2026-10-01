from functools import lru_cache
from typing import Literal
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

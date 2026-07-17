"""Configuration settings for the Instagram monitor application.

Design Decisions:
    - Pydantic Settings validates types at startup, catching misconfigurations early.
    - @lru_cache on get_settings() ensures a single Settings instance across the app
      (poor man's singleton without global mutable state).
    - database_url property constructs the async connection string from components,
      but allows a full URL override via db_url for flexibility.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables or .env file.

    All fields map to environment variables (case-insensitive).
    Required fields with no default will cause a startup error if missing — this is
    intentional. We want to fail fast, not at 3am when a check can't connect.

    Attributes:
        db_host: PostgreSQL / Supabase database host.
        db_port: PostgreSQL database port.
        db_name: PostgreSQL database name.
        db_user: PostgreSQL database user.
        db_password: PostgreSQL database password.
        db_url: Complete database URL (optional override).
        telegram_bot_token: Telegram bot API token from @BotFather.
        telegram_chat_id: Telegram chat ID for notifications.
        check_interval_seconds: Seconds between monitoring cycles.
        check_delay_seconds: Seconds between individual username checks.
        max_concurrent_checks: Max parallel Instagram requests.
        log_level: Logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory for log file storage.
        environment: Deployment environment identifier.
    """

    # --- Supabase / PostgreSQL ---
    db_host: str
    db_port: int = 5432
    db_name: str = "postgres"
    db_user: str = "postgres"
    db_password: str
    db_url: Optional[str] = None

    # --- Telegram ---
    telegram_bot_token: str
    telegram_chat_id: str

    # --- Monitoring ---
    check_interval_seconds: int = 300
    check_delay_seconds: int = 10
    max_concurrent_checks: int = 3

    # --- Application ---
    log_level: str = "INFO"
    log_dir: str = "logs"
    environment: str = "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def database_url(self) -> str:
        """Construct the async PostgreSQL connection string.

        If db_url is explicitly set, it takes priority. Otherwise, the URL
        is built from individual components. This allows both simple .env
        configs and full connection string overrides.

        Returns:
            Async-compatible PostgreSQL connection string for asyncpg.
        """
        if self.db_url:
            return self.db_url
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        """Construct the synchronous PostgreSQL connection string.

        Used for Alembic migrations and other sync-only operations.

        Returns:
            Sync-compatible PostgreSQL connection string.
        """
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache()
def get_settings() -> Settings:
    """Get the cached application settings singleton.

    Uses @lru_cache to ensure only one Settings instance exists.
    This avoids global mutable state while providing singleton behavior.

    Returns:
        The Settings object populated from environment variables / .env file.

    Raises:
        pydantic.ValidationError: If required settings are missing or invalid.
    """
    return Settings()

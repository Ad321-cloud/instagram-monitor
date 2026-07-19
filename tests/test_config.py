"""Tests for the configuration system."""

import os
from unittest.mock import patch

import pytest


class TestSettings:
    """Test suite for the Settings configuration class."""

    def _make_env(self, **overrides: str) -> dict[str, str]:
        """Create a minimal valid environment dictionary.

        Args:
            **overrides: Key-value pairs to override defaults.

        Returns:
            Environment dictionary with required settings.
        """
        base = {
            "DB_HOST": "db.test.supabase.co",
            "DB_PORT": "5432",
            "DB_NAME": "postgres",
            "DB_USER": "postgres",
            "DB_PASSWORD": "test-password",
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "TELEGRAM_CHAT_ID": "987654321",
        }
        base.update(overrides)
        return base

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_required_fields_raises(self) -> None:
        """Settings should fail if required fields are missing."""
        from app.config.settings import Settings
        with pytest.raises(Exception):
            Settings()

    @patch.dict(os.environ, clear=True)
    def test_loads_from_env(self) -> None:
        """Settings should load all fields from environment variables."""
        env = self._make_env()
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            assert s.db_host == "db.test.supabase.co"
            assert s.db_port == 5432
            assert s.telegram_bot_token == "123456:ABC-DEF"
            assert s.telegram_chat_id == "987654321"

    @patch.dict(os.environ, clear=True)
    def test_database_url_construction(self) -> None:
        """database_url property should construct a valid asyncpg URL."""
        env = self._make_env()
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            url = s.database_url
            assert url.startswith("postgresql+asyncpg://")
            assert "test-password" in url
            assert "db.test.supabase.co" in url

    @patch.dict(os.environ, clear=True)
    def test_database_url_override(self) -> None:
        """db_url should override the constructed database URL."""
        env = self._make_env(DB_URL="postgresql+asyncpg://custom:url@host/db")
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            assert s.database_url == "postgresql+asyncpg://custom:url@host/db"

    @patch.dict(os.environ, clear=True)
    def test_database_url_render_name_override(self) -> None:
        """Render's DATABASE_URL variable should override component settings."""
        env = {
            "DATABASE_URL": "postgres://user:password@pooler.supabase.com:5432/postgres?ssl=require",
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "TELEGRAM_CHAT_ID": "987654321",
        }
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            assert s.database_url == env["DATABASE_URL"].replace(
                "postgres://", "postgresql+asyncpg://", 1
            )

    @patch.dict(os.environ, clear=True)
    def test_database_components_remain_supported(self) -> None:
        """Individual DB_* variables still construct the connection URL."""
        env = self._make_env()
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            assert "db.test.supabase.co" in s.database_url

    @patch.dict(os.environ, clear=True)
    def test_strips_pasted_database_values(self) -> None:
        """Dashboard values copied with surrounding whitespace remain usable."""
        env = self._make_env(
            DB_HOST="  aws-1-ap-south-1.pooler.supabase.com\n",
            DB_PASSWORD=" password ",
        )
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            assert s.db_host == "aws-1-ap-south-1.pooler.supabase.com"
            assert s.db_password == "password"

    @patch.dict(os.environ, clear=True)
    def test_defaults(self) -> None:
        """Default values should be applied for optional settings."""
        env = self._make_env()
        with patch.dict(os.environ, env):
            from app.config.settings import Settings
            s = Settings()
            assert s.check_interval_seconds == 300
            assert s.check_delay_seconds == 10
            assert s.max_concurrent_checks == 3
            assert s.log_level == "INFO"
            assert s.environment == "development"

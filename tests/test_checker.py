"""Tests for the Instagram checker module."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.checker.instagram import CheckResult, InstagramChecker


class TestCheckResult:
    """Test suite for the CheckResult dataclass."""

    def test_default_values(self) -> None:
        """CheckResult should have sensible defaults."""
        result = CheckResult(username="test", status="active")
        assert result.username == "test"
        assert result.status == "active"
        assert result.http_status_code is None
        assert result.response_time_ms == 0.0
        assert result.error is None
        assert isinstance(result.checked_at, datetime)

    def test_custom_values(self) -> None:
        """CheckResult should accept custom values."""
        result = CheckResult(
            username="test",
            status="available",
            http_status_code=404,
            response_time_ms=150.5,
        )
        assert result.http_status_code == 404
        assert result.response_time_ms == 150.5


class TestInstagramChecker:
    """Test suite for the InstagramChecker class."""

    def test_init_defaults(self) -> None:
        """Checker should initialize with default delay."""
        checker = InstagramChecker()
        assert checker._check_delay == 10.0

    def test_init_custom_delay(self) -> None:
        """Checker should accept custom delay."""
        checker = InstagramChecker(check_delay=5.0)
        assert checker._check_delay == 5.0

    def test_interpret_status_200(self) -> None:
        """HTTP 200 should map to 'active'."""
        checker = InstagramChecker()
        assert checker._interpret_status(200) == "active"

    def test_interpret_status_404(self) -> None:
        """HTTP 404 should map to 'available'."""
        checker = InstagramChecker()
        assert checker._interpret_status(404) == "available"

    def test_interpret_status_302(self) -> None:
        """HTTP 302 redirect is ambiguous and should not disable a profile."""
        checker = InstagramChecker()
        assert checker._interpret_status(302) == "unknown"

    @pytest.mark.asyncio
    async def test_http_200_login_page_is_not_marked_disabled(self) -> None:
        """Instagram's generic 200 login page must not become unavailable."""
        checker = InstagramChecker()
        checker._do_request = AsyncMock(
            return_value=(200, 120.0, "<html><title>Instagram</title></html>")
        )
        result = await checker.check_username("live_profile")
        assert result.status == "active"
        await checker.close()

    def test_interpret_status_429(self) -> None:
        """HTTP 429 rate limit should map to 'unknown'."""
        checker = InstagramChecker()
        assert checker._interpret_status(429) == "unknown"

    def test_interpret_status_500(self) -> None:
        """HTTP 500 should map to 'unknown'."""
        checker = InstagramChecker()
        assert checker._interpret_status(500) == "unknown"

    def test_jittered_delay_range(self) -> None:
        """Jittered delay should be within ±30% of base delay."""
        checker = InstagramChecker(check_delay=10.0)
        for _ in range(100):
            delay = checker._jittered_delay()
            assert 7.0 <= delay <= 13.0, f"Delay {delay} out of expected range"

    @pytest.mark.asyncio
    async def test_check_many_empty_list(self) -> None:
        """check_many should return empty list for empty input."""
        async with InstagramChecker() as checker:
            results = await checker.check_many([])
            assert results == []

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Checker should work as an async context manager."""
        async with InstagramChecker() as checker:
            assert checker._session is None  # Lazy init
        # After exit, session should be cleaned up

    @pytest.mark.asyncio
    async def test_check_username_network_error(self) -> None:
        """check_username should return 'unknown' on network errors."""
        checker = InstagramChecker()
        # Mock _do_request to raise a connection error
        import aiohttp
        checker._do_request = AsyncMock(
            side_effect=aiohttp.ClientError("Connection failed")
        )
        result = await checker.check_username("test_user")
        assert result.status == "unknown"
        assert result.error is not None
        await checker.close()

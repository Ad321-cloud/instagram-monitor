"""Tests for the Telegram notifier module."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.notifier.telegram import TelegramNotifier


class TestTelegramNotifier:
    """Test suite for the TelegramNotifier class."""

    def _make_notifier(self) -> TelegramNotifier:
        """Create a notifier with test credentials."""
        return TelegramNotifier(
            bot_token="test-token",
            chat_id="test-chat-id",
        )

    @pytest.mark.asyncio
    async def test_send_status_change_success(self) -> None:
        """send_status_change should return True on success."""
        notifier = self._make_notifier()
        notifier._send_message = AsyncMock(return_value=True)

        result = await notifier.send_status_change(
            username="test_user",
            old_status="active",
            new_status="available",
        )
        assert result is True
        notifier._send_message.assert_called_once()

        # Verify message content
        call_args = notifier._send_message.call_args[0][0]
        assert "test_user" in call_args
        assert "AVAILABLE" in call_args
        assert "🔵" in call_args

    @pytest.mark.asyncio
    async def test_send_status_change_failure(self) -> None:
        """send_status_change should return False on failure."""
        notifier = self._make_notifier()
        notifier._send_message = AsyncMock(side_effect=Exception("API error"))

        result = await notifier.send_status_change(
            username="test_user",
            old_status="active",
            new_status="available",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_send_startup_notification(self) -> None:
        """send_startup_notification should send a formatted message."""
        notifier = self._make_notifier()
        notifier._send_message = AsyncMock(return_value=True)

        result = await notifier.send_startup_notification()
        assert result is True

        call_args = notifier._send_message.call_args[0][0]
        assert "Monitor Started" in call_args

    @pytest.mark.asyncio
    async def test_send_error_alert(self) -> None:
        """send_error_alert should include the error message."""
        notifier = self._make_notifier()
        notifier._send_message = AsyncMock(return_value=True)

        result = await notifier.send_error_alert("Something broke")
        assert result is True

        call_args = notifier._send_message.call_args[0][0]
        assert "Something broke" in call_args
        assert "Error" in call_args

    @pytest.mark.asyncio
    async def test_send_alert_generic(self) -> None:
        """send_alert should send a generic alert message."""
        notifier = self._make_notifier()
        notifier._send_message = AsyncMock(return_value=True)

        result = await notifier.send_alert("Custom alert message")
        assert result is True

        call_args = notifier._send_message.call_args[0][0]
        assert "Custom alert message" in call_args

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """close should set _bot to None."""
        notifier = self._make_notifier()
        notifier._bot = MagicMock()
        await notifier.close()
        assert notifier._bot is None


class TestStatusEmojis:
    """Test that all status types have proper emoji mappings."""

    def test_all_statuses_mapped(self) -> None:
        """All UsernameStatus values should have emoji and display mappings."""
        from app.notifier.telegram import _STATUS_EMOJI, _STATUS_DISPLAY

        expected_statuses = ["active", "available", "unavailable", "unknown"]
        for status in expected_statuses:
            assert status in _STATUS_EMOJI, f"Missing emoji for '{status}'"
            assert status in _STATUS_DISPLAY, f"Missing display for '{status}'"

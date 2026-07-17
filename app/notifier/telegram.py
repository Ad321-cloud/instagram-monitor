"""Telegram notification system for Instagram monitoring alerts.

Design Decisions:
    - python-telegram-bot v20+ (async native) — no sync-to-async wrappers needed.
    - Lazy bot initialization — avoids hitting Telegram API at import time.
    - HTML parse mode — more readable and flexible than MarkdownV2 (no escaping hell).
    - Each method returns bool — callers decide whether to retry or log failures.
    - tenacity retry on send — Telegram's API occasionally returns 5xx or has
      transient network issues. 3 retries with backoff handles this gracefully.
    - All errors caught and logged — Telegram failures should never crash the monitor.
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from telegram import Bot
from telegram.error import TelegramError


# Emoji indicators for each status
_STATUS_EMOJI: dict[str, str] = {
    "active": "🟢",
    "available": "🔵",
    "unavailable": "🔴",
    "unknown": "⚪",
}

# Status display names for messages
_STATUS_DISPLAY: dict[str, str] = {
    "active": "ACTIVE",
    "available": "AVAILABLE",
    "unavailable": "UNAVAILABLE",
    "unknown": "UNKNOWN",
}


class TelegramNotifier:
    """Sends formatted notifications to a Telegram chat.

    Handles status change alerts, startup notifications, error alerts,
    and generic messages. All methods are async and return bool indicating
    success/failure.

    Usage:
        notifier = TelegramNotifier(bot_token="...", chat_id="...")
        await notifier.send_startup_notification()
        await notifier.send_status_change("target_user", "active", "available")
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        """Initialize the Telegram notifier.

        Args:
            bot_token: Telegram bot API token from @BotFather.
            chat_id: Telegram chat ID to send messages to.
        """
        self._bot_token: str = bot_token
        self._chat_id: str = chat_id
        self._bot: Optional[Bot] = None

    async def _get_bot(self) -> Bot:
        """Get or create the Telegram Bot instance.

        Lazily initialized to avoid API calls at construction time.

        Returns:
            The python-telegram-bot Bot instance.
        """
        if self._bot is None:
            self._bot = Bot(token=self._bot_token)
        return self._bot

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((TelegramError, ConnectionError, OSError)),
        reraise=True,
    )
    async def _send_message(self, text: str) -> bool:
        """Send a message via Telegram with retry logic.

        Uses HTML parse mode for rich formatting.

        Args:
            text: HTML-formatted message text.

        Returns:
            True if the message was sent successfully.

        Raises:
            TelegramError: After all retry attempts exhausted.
        """
        bot = await self._get_bot()
        await bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode="HTML",
        )
        return True

    async def send_status_change(
        self,
        username: str,
        old_status: str,
        new_status: str,
    ) -> bool:
        """Send a status change notification.

        Formats a rich message with emoji indicators, Instagram link,
        and timestamp.

        Args:
            username: The Instagram username that changed state.
            old_status: The previous status value.
            new_status: The new status value.

        Returns:
            True if the notification was sent, False on failure.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        new_emoji = _STATUS_EMOJI.get(new_status, "⚪")
        old_emoji = _STATUS_EMOJI.get(old_status, "⚪")
        new_display = _STATUS_DISPLAY.get(new_status, new_status.upper())
        old_display = _STATUS_DISPLAY.get(old_status, old_status.upper())

        # Determine headline based on new status
        if new_status == "available":
            headline = "🔵 Username Available!"
        elif new_status == "active":
            headline = "🟢 Username Active"
        elif new_status == "unavailable":
            headline = "🔴 Username Unavailable"
        else:
            headline = "⚪ Status Changed"

        message = (
            f"<b>{headline}</b>\n"
            f"\n"
            f"<b>@{username}</b> is now <b>{new_display}</b>\n"
            f"Previous: {old_emoji} {old_display}\n"
            f"\n"
            f"🔗 <a href='https://instagram.com/{username}'>View Profile</a>\n"
            f"🕐 {now}"
        )

        try:
            await self._send_message(message)
            logger.info(
                "Telegram notification sent: {} status {} -> {}",
                username,
                old_status,
                new_status,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to send Telegram notification for {}: {}",
                username,
                e,
            )
            return False

    async def send_alert(self, message: str) -> bool:
        """Send a generic alert message.

        Args:
            message: Plain text message content.

        Returns:
            True if sent successfully, False on failure.
        """
        text = f"ℹ️ <b>Monitor Alert</b>\n\n{message}"
        try:
            await self._send_message(text)
            logger.info("Alert sent: {}", message[:50])
            return True
        except Exception as e:
            logger.error("Failed to send alert: {}", e)
            return False

    async def send_startup_notification(self) -> bool:
        """Send a notification that the monitoring service has started.

        Returns:
            True if sent successfully, False on failure.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            "🚀 <b>Instagram Monitor Started</b>\n"
            "\n"
            "The monitoring service is now running.\n"
            f"🕐 {now}"
        )
        try:
            await self._send_message(message)
            logger.info("Startup notification sent")
            return True
        except Exception as e:
            logger.error("Failed to send startup notification: {}", e)
            return False

    async def send_error_alert(self, error: str) -> bool:
        """Send an error notification for critical failures.

        Args:
            error: Error description.

        Returns:
            True if sent successfully, False on failure.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            "⚠️ <b>Monitor Error</b>\n"
            "\n"
            f"<code>{error}</code>\n"
            f"\n"
            f"🕐 {now}"
        )
        try:
            await self._send_message(message)
            logger.warning("Error alert sent: {}", error[:50])
            return True
        except Exception as e:
            logger.error("Failed to send error alert: {}", e)
            return False

    async def close(self) -> None:
        """Clean up the bot instance."""
        if self._bot:
            logger.debug("Telegram notifier closed")
            self._bot = None

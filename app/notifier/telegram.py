"""Interactive Telegram bot for controlling the Instagram monitoring system.

Design Decisions:
    - python-telegram-bot v20+ Application with command handlers — the bot IS the
      user interface, not just a notification channel.
    - Commands: /monitor, /demonitor, /status, /list, /check, /help
    - Each command handler receives the shared checker + repository via application
      context (bot_data) — no globals.
    - The bot runs its polling loop concurrently with the monitoring scheduler.
    - All responses use HTML parse mode for rich formatting.
    - Immediate check on /monitor — user gets instant feedback, not "added, wait 5min".
"""

from datetime import datetime, timezone
from io import BytesIO
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Bot, InputFile, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from telegram.error import TelegramError

from app.models.username import UsernameStatus


# Emoji indicators for each status
_STATUS_EMOJI: dict[str, str] = {
    "active": "✅",
    "available": "✨",
    "unavailable": "⛔",
    "unknown": "❔",
}

_STATUS_DISPLAY: dict[str, str] = {
    "active": "ACTIVE",
    "available": "AVAILABLE",
    "unavailable": "UNAVAILABLE / DISABLED",
    "unknown": "UNKNOWN",
}


def _format_followers(count: Optional[int]) -> str:
    """Format follower count for display.

    Args:
        count: Raw follower count or None.

    Returns:
        Formatted string like "1.2K", "3.5M", or "N/A".
    """
    if count is None:
        return "N/A"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _format_time(dt: Optional[datetime]) -> str:
    """Format a datetime for display in IST.

    Args:
        dt: Datetime object or None.

    Returns:
        Formatted timestamp string or "Never".
    """
    if dt is None:
        return "Never"
    
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    ist_dt = dt.astimezone(ZoneInfo("Asia/Kolkata"))
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")


def _profile_card(result: "CheckResult") -> str:  # noqa: F821
    """Build the rich Instagram-information card used for profile lookups."""
    status = _STATUS_DISPLAY.get(result.status, result.status.upper())
    status_icon = _STATUS_EMOJI.get(result.status, "⚪")
    private = (
        "PRIVATE" if result.is_private else "PUBLIC"
        if result.is_private is not None else "N/A"
    )
    verified = " ✅" if result.status == "active" else ""
    account_type = escape(result.account_type.upper()) if result.account_type else "N/A"
    bio = escape(result.biography) if result.biography else "No bio available"
    full_name = escape(result.full_name) if result.full_name else "N/A"
    user_id = escape(result.user_id) if result.user_id else "N/A"

    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "📸 <b>INSTAGRAM INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>USERNAME</b>  » @{escape(result.username)}{verified}\n"
        f"👑 <b>NAME</b>  » {full_name}\n"
        f"🆔 <b>USER ID</b>  » <code>{user_id}</code>\n"
        f"🔒 <b>STATUS</b>  » {status_icon} {status} ({private})\n"
        f"📁 <b>ACCOUNT TYPE</b>  » {account_type}\n"
        f"🔗 <b>PROFILE</b>  » <a href='https://instagram.com/{escape(result.username)}'>VIEW PROFILE</a>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>FOLLOWERS</b>  » {_format_followers(result.follower_count)}\n"
        f"🔄 <b>FOLLOWING</b>  » {_format_followers(result.following_count)}\n"
        f"📱 <b>POSTS</b>  » {_format_followers(result.post_count)}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💬 <b>BIO</b>\n"
        f"{bio}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🚀 <b>RESPONSE TIME</b>  » {result.response_time_ms / 1000:.2f}s\n"
        f"🕐 {_format_time(result.checked_at)}\n"
        "━━━━━━━━━━━━━━━━━━"
    )


class TelegramBot:
    """Interactive Telegram bot for controlling the Instagram monitor.

    Provides command handlers for:
        /monitor <username> — Start monitoring a username (+ immediate check)
        /demonitor <username> — Stop monitoring a username
        /status <username> — Check current status of a username
        /list — List all monitored usernames
        /check — Run a check cycle on all active usernames
        /help — Show available commands

    Also provides methods for the scheduler to send notifications:
        send_status_change() — Alert on state changes
        send_error_alert() — Alert on errors

    Attributes:
        _bot_token: Telegram bot API token.
        _chat_id: Authorized chat ID for commands and notifications.
        _app: python-telegram-bot Application instance.
        _repository: Database repository (set during setup).
        _checker: Instagram checker (set during setup).
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        """Initialize the bot.

        Args:
            bot_token: Telegram bot API token from @BotFather.
            chat_id: Authorized Telegram chat ID.
        """
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None

        # These are set via setup() before the bot starts
        self._repository = None
        self._checker = None

    def setup(self, repository: "UsernameRepository", checker: "InstagramChecker") -> None:  # noqa: F821
        """Inject dependencies before starting the bot.

        Args:
            repository: Database repository for username operations.
            checker: Instagram checker for status checks.
        """
        self._repository = repository
        self._checker = checker

    async def _build_app(self) -> Application:
        """Build the telegram Application with command handlers.

        Returns:
            Configured Application instance.
        """
        app = (
            Application.builder()
            .token(self._bot_token)
            .build()
        )

        # Store dependencies in bot_data for handler access
        app.bot_data["repository"] = self._repository
        app.bot_data["checker"] = self._checker
        app.bot_data["chat_id"] = self._chat_id

        # Register command handlers
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("monitor", self._cmd_monitor))
        app.add_handler(CommandHandler("demonitor", self._cmd_demonitor))
        app.add_handler(CommandHandler("markactive", self._cmd_mark_active))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("list", self._cmd_list))
        app.add_handler(CommandHandler("check", self._cmd_check))

        # Catch unknown commands
        app.add_handler(MessageHandler(filters.COMMAND, self._cmd_unknown))

        return app

    # ── Command Handlers ──────────────────────────────────────────────

    @staticmethod
    async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        msg = (
            "🔍 <b>Instagram Monitor Bot</b>\n"
            "\n"
            "I monitor Instagram usernames and alert you when they change state.\n"
            "\n"
            "📋 <b>Commands:</b>\n"
            "/monitor <code>username</code> — Start monitoring\n"
            "/demonitor <code>username</code> — Stop monitoring\n"
            "/markactive <code>username</code> — Set a known-live profile active\n"
            "/status <code>username</code> — Check status now\n"
            "/list — Show all monitored usernames\n"
            "/check — Run a check on all usernames\n"
            "/help — Show this message"
        )
        await update.message.reply_text(msg, parse_mode="HTML")

    @staticmethod
    async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        msg = (
            "📖 <b>Commands</b>\n"
            "\n"
            "▸ <code>/monitor username</code>\n"
            "  Start monitoring an Instagram username.\n"
            "  Does an immediate check and shows current status.\n"
            "\n"
            "▸ <code>/demonitor username</code>\n"
            "  Stop monitoring a username.\n"
            "\n"
            "▸ <code>/markactive username</code>\n"
            "  Reset a known-live profile to ACTIVE when Instagram blocks Render checks.\n"
            "\n"
            "▸ <code>/status username</code>\n"
            "  Check a username's current status right now.\n"
            "  Shows: status, followers, disabled/returned times.\n"
            "\n"
            "▸ <code>/list</code>\n"
            "  Show all monitored usernames with their status.\n"
            "\n"
            "▸ <code>/check</code>\n"
            "  Run a check cycle on all active usernames.\n"
            "\n"
            "💡 <b>Status meanings:</b>\n"
            "🟢 Active — Profile is live and public\n"
            "🔵 Available — Username is not taken\n"
            "🔴 Unavailable — Disabled / suspended / private\n"
            "⚪ Unknown — Could not determine (rate limited)"
        )
        await update.message.reply_text(msg, parse_mode="HTML")

    @staticmethod
    async def _cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /monitor <username> command.

        Adds the username to monitoring and does an immediate check.
        """
        if not context.args:
            await update.message.reply_text(
                "⚠️ Usage: <code>/monitor username</code>", parse_mode="HTML"
            )
            return

        username = context.args[0].lower().strip().lstrip("@")
        repo = context.bot_data["repository"]
        checker = context.bot_data["checker"]

        # Check if already monitored
        existing = await repo.get_by_username(username)
        if existing and existing.is_active:
            await update.message.reply_text(
                f"⚠️ <b>@{username}</b> is already being monitored.",
                parse_mode="HTML",
            )
            return

        try:
            # Reactivate or add new
            if existing and not existing.is_active:
                await repo.reactivate_username(username)
                await update.message.reply_text(
                    f"♻️ <b>@{username}</b> reactivated for monitoring.",
                    parse_mode="HTML",
                )
            else:
                await repo.add_username(username)
                await update.message.reply_text(
                    f"✅ <b>@{username}</b> added to monitoring.",
                    parse_mode="HTML",
                )

            # Immediate check
            await update.message.reply_text("🔄 Checking now...", parse_mode="HTML")
            result = await checker.check_username(username)
            # Update DB with first check result
            from app.models.username import UsernameStatus
            try:
                status_enum = UsernameStatus(result.status)
            except ValueError:
                status_enum = UsernameStatus.UNKNOWN

            await repo.update_status(
                username=username,
                new_status=status_enum,
                http_code=result.http_status_code,
                response_time=result.response_time_ms,
                follower_count=result.follower_count,
            )

            await TelegramBot._reply_with_profile_card(update, result)
            await update.message.reply_text(
                "📡 Now monitoring — you’ll be notified of any changes.", parse_mode="HTML"
            )

        except Exception as e:
            logger.error("Error in /monitor for {}: {}", username, e)
            await update.message.reply_text(
                f"❌ Error: {e}", parse_mode="HTML"
            )

    @staticmethod
    async def _cmd_demonitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /demonitor <username> command."""
        if not context.args:
            await update.message.reply_text(
                "⚠️ Usage: <code>/demonitor username</code>", parse_mode="HTML"
            )
            return

        username = context.args[0].lower().strip().lstrip("@")
        repo = context.bot_data["repository"]

        try:
            removed = await repo.remove_username(username)
            if removed:
                await update.message.reply_text(
                    f"🛑 <b>@{username}</b> removed from monitoring.",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"⚠️ <b>@{username}</b> is not being monitored.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error("Error in /demonitor for {}: {}", username, e)
            await update.message.reply_text(f"❌ Error: {e}", parse_mode="HTML")

    @staticmethod
    async def _cmd_mark_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manually set a confirmed-live profile as the reliable baseline."""
        if not context.args:
            await update.message.reply_text(
                "⚠️ Usage: <code>/markactive username</code>", parse_mode="HTML"
            )
            return

        username = context.args[0].lower().strip().lstrip("@")
        repo = context.bot_data["repository"]

        try:
            record = await repo.get_by_username(username)
            if not record:
                await update.message.reply_text(
                    f"⚠️ <b>@{username}</b> is not being monitored. Use /monitor first.",
                    parse_mode="HTML",
                )
                return

            await repo.update_status(
                username=username,
                new_status=UsernameStatus.ACTIVE,
            )
            await update.message.reply_text(
                f"🟢 <b>@{username}</b> set to ACTIVE.\n"
                "Future blocked checks will retain this status.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Error marking {} active: {}", username, e)
            await update.message.reply_text(f"❌ Error: {e}", parse_mode="HTML")

    @staticmethod
    async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status <username> command.

        Does a live check and shows current status with details.
        """
        if not context.args:
            await update.message.reply_text(
                "⚠️ Usage: <code>/status username</code>", parse_mode="HTML"
            )
            return

        username = context.args[0].lower().strip().lstrip("@")
        checker = context.bot_data["checker"]
        repo = context.bot_data["repository"]

        await update.message.reply_text("🔄 Checking...", parse_mode="HTML")

        try:
            result = await checker.check_username(username)
            db_record = await repo.get_by_username(username)
            await TelegramBot._reply_with_profile_card(update, result)
            if db_record and (db_record.disabled_at or db_record.returned_at):
                history = ""
                if db_record.disabled_at:
                    history += f"🔴 Last disabled: <b>{_format_time(db_record.disabled_at)}</b>\n"
                if db_record.returned_at:
                    history += f"🟢 Last returned: <b>{_format_time(db_record.returned_at)}</b>"
                await update.message.reply_text(history, parse_mode="HTML")

        except Exception as e:
            logger.error("Error in /status for {}: {}", username, e)
            await update.message.reply_text(f"❌ Error: {e}", parse_mode="HTML")

    @staticmethod
    async def _cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list command — show all monitored usernames."""
        repo = context.bot_data["repository"]

        try:
            usernames = await repo.get_all_active()

            if not usernames:
                await update.message.reply_text(
                    "📋 No usernames are being monitored.\n\n"
                    "Use <code>/monitor username</code> to add one.",
                    parse_mode="HTML",
                )
                return

            header = (
                "📋 <b>MONITORED ACCOUNTS</b>\n"
                f"<b>Total:</b> {len(usernames)}\n"
                "━━━━━━━━━━━━━━━━━━\n"
            )
            blocks: list[str] = []
            for index, u in enumerate(usernames, start=1):
                emoji = _STATUS_EMOJI.get(u.current_status, "❔")
                status = _STATUS_DISPLAY.get(u.current_status, u.current_status.upper())
                history = ""
                if u.disabled_at:
                    history += f"\n   ⛔ Disabled: {_format_time(u.disabled_at)}"
                if u.returned_at:
                    history += f"\n   🏆 Returned: {_format_time(u.returned_at)}"
                blocks.append(
                    f"<b>{index}. {emoji} @{escape(u.username)}</b>\n"
                    f"   Status: <code>{status}</code>\n"
                    f"   👥 Followers: <b>{_format_followers(u.follower_count)}</b>\n"
                    f"   🕐 Last check: {_format_time(u.last_checked_at)}"
                    f"{history}\n"
                    f"   🔗 <a href='https://instagram.com/{escape(u.username)}'>View profile</a>"
                )

            # Send one account per Telegram message so every monitored profile
            # is easy to scan and no long list becomes visually crowded.
            await update.message.reply_text(header.rstrip(), parse_mode="HTML")
            for block in blocks:
                await update.message.reply_text(
                    f"{block}\n━━━━━━━━━━━━━━━━━━",
                    parse_mode="HTML",
                )

        except Exception as e:
            logger.error("Error in /list: {}", e)
            await update.message.reply_text(f"❌ Error: {e}", parse_mode="HTML")

    @staticmethod
    async def _cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /check command — run a check cycle on all active usernames."""
        repo = context.bot_data["repository"]
        checker = context.bot_data["checker"]

        try:
            usernames = await repo.get_all_active()
            if not usernames:
                await update.message.reply_text(
                    "📋 No active usernames to check.", parse_mode="HTML"
                )
                return

            username_list = [u.username for u in usernames]
            await update.message.reply_text(
                f"🔄 Checking {len(username_list)} usernames...\nThis may take a moment.",
                parse_mode="HTML",
            )

            results = await checker.check_many(username_list, max_concurrent=3)

            msg = "📊 <b>Check Results</b>\n\n"
            for result in results:
                followers = _format_followers(result.follower_count)

                # Update DB
                from app.models.username import UsernameStatus
                try:
                    status_enum = UsernameStatus(result.status)
                except ValueError:
                    status_enum = UsernameStatus.UNKNOWN

                await repo.update_status(
                    username=result.username,
                    new_status=status_enum,
                    http_code=result.http_status_code,
                    response_time=result.response_time_ms,
                    follower_count=result.follower_count,
                )

                # If Instagram blocks the request, show the last reliable state.
                stored = await repo.get_by_username(result.username)
                display_status = (
                    stored.current_status
                    if result.status == "unknown" and stored
                    else result.status
                )
                emoji = _STATUS_EMOJI.get(display_status, "⚪")
                suffix = " (last reliable)" if display_status != result.status else ""
                msg += (
                    f"{emoji} <b>@{result.username}</b> — "
                    f"{display_status.upper()}{suffix} | 👥 {followers}\n"
                )

            await update.message.reply_text(msg, parse_mode="HTML")

        except Exception as e:
            logger.error("Error in /check: {}", e)
            await update.message.reply_text(f"❌ Error: {e}", parse_mode="HTML")

    @staticmethod
    async def _cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle unknown commands."""
        await update.message.reply_text(
            "❓ Unknown command. Use /help to see available commands.",
            parse_mode="HTML",
        )

    @staticmethod
    async def _reply_with_profile_card(update: Update, result: "CheckResult") -> None:  # noqa: F821
        """Reply with the profile photo and information card when available."""
        card = _profile_card(result)
        if result.profile_pic_url:
            try:
                await update.message.reply_photo(
                    photo=result.profile_pic_url,
                    caption=card,
                    parse_mode="HTML",
                )
                return
            except TelegramError as e:
                logger.warning("Could not send profile image for {}: {}", result.username, e)
        await update.message.reply_text(card, parse_mode="HTML")

    # ── Notification Methods (called by the scheduler) ────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((TelegramError, ConnectionError, OSError)),
        reraise=True,
    )
    async def _send_message(self, text: str) -> bool:
        """Send a message via Telegram with retry logic.

        Args:
            text: HTML-formatted message text.

        Returns:
            True if sent successfully.
        """
        if self._bot is None:
            self._bot = Bot(token=self._bot_token)
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode="HTML",
        )
        return True

    async def _send_return_screenshot(self, username: str) -> bool:
        """Capture and send a small public Instagram profile screenshot.

        Screenshot capture is deliberately best-effort: Instagram may present a
        login/challenge page, but that must never prevent the return alert itself.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright is not installed; skipping screenshot for @{}", username)
            return False

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(
                        viewport={"width": 760, "height": 340},
                        device_scale_factor=1,
                        color_scheme="dark",
                    )
                    page = await context.new_page()
                    await page.goto(
                        f"https://www.instagram.com/{username}/",
                        wait_until="domcontentloaded",
                        timeout=20_000,
                    )
                    await page.wait_for_timeout(1500)
                    # Instagram often places a sign-up/login dialog over the
                    # public profile. Close it so the notification contains
                    # the profile page rather than the promotional overlay.
                    await page.keyboard.press("Escape")
                    for selector in (
                        'button[aria-label="Close"]',
                        '[role="dialog"] button[aria-label="Close"]',
                    ):
                        close_button = page.locator(selector).first
                        if await close_button.count():
                            try:
                                await close_button.click(timeout=2_000)
                            except Exception:
                                pass
                    await page.wait_for_timeout(500)
                    image = await page.screenshot(type="jpeg", quality=88, full_page=False)
                finally:
                    if "context" in locals():
                        await context.close()
                    await browser.close()

            if self._bot is None:
                self._bot = Bot(token=self._bot_token)
            await self._bot.send_photo(
                chat_id=self._chat_id,
                photo=InputFile(BytesIO(image), filename=f"{username}-returned.jpg"),
                caption=f"📸 <b>@{escape(username)}</b> profile after returning online",
                parse_mode="HTML",
            )
            logger.info("Return screenshot sent for @{}", username)
            return True
        except Exception as e:
            logger.warning("Could not capture return screenshot for @{}: {}", username, e)
            return False

    async def send_status_change(
        self,
        username: str,
        old_status: str,
        new_status: str,
        follower_count: Optional[int] = None,
        disabled_at: Optional[datetime] = None,
        returned_at: Optional[datetime] = None,
    ) -> bool:
        """Send a status change notification.

        Includes follower count and exact disable/return timestamps.

        Args:
            username: The Instagram username.
            old_status: Previous status.
            new_status: New status.
            follower_count: Current follower count if available.
            disabled_at: When the profile was disabled.
            returned_at: When the profile returned.

        Returns:
            True if sent, False on failure.
        """
        now_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        new_emoji = _STATUS_EMOJI.get(new_status, "⚪")
        old_emoji = _STATUS_EMOJI.get(old_status, "⚪")
        new_display = _STATUS_DISPLAY.get(new_status, new_status.upper())
        old_display = _STATUS_DISPLAY.get(old_status, old_status.upper())
        followers = _format_followers(follower_count)

        # Determine headline and details based on transition
        if new_status == "unavailable":
            headline = "⛔ Profile Restricted"
            detail = f"<b>@{username}</b> has been <b>DISABLED</b>"
            if disabled_at:
                detail += f"\n⏰ Disabled at: <b>{_format_time(disabled_at)}</b>"
        elif old_status == "unavailable" and new_status == "active":
            headline = "🏆✅ Account Recovered!"
            detail = f"<b>@{username}</b> is <b>BACK ONLINE</b> again"
            if returned_at:
                detail += f"\n⏰ Returned at: <b>{_format_time(returned_at)}</b>"
            if disabled_at:
                detail += f"\n🔴 Was disabled since: {_format_time(disabled_at)}"
        elif new_status == "available":
            headline = "✨ Username Available!"
            detail = f"<b>@{username}</b> is now <b>AVAILABLE</b>"
        elif new_status == "active":
            headline = "✅ Username Active"
            detail = f"<b>@{username}</b> is now <b>ACTIVE</b>"
        else:
            headline = "⚪ Status Changed"
            detail = f"<b>@{username}</b> status changed"

        message = (
            f"<b>{headline}</b>\n"
            f"\n"
            f"{detail}\n"
            f"\n"
            f"Previous: {old_emoji} {old_display}\n"
            f"Current: {new_emoji} {new_display}\n"
            f"👥 Followers: <b>{followers}</b>\n"
            f"\n"
            f"🔗 <a href='https://instagram.com/{username}'>View Profile</a>\n"
            f"🕐 {now_str}"
        )

        try:
            await self._send_message(message)
            if old_status == "unavailable" and new_status == "active":
                await self._send_return_screenshot(username)
            logger.info(
                "Status change notification sent: {} {} -> {}",
                username, old_status, new_status,
            )
            return True
        except Exception as e:
            logger.error("Failed to send notification for {}: {}", username, e)
            return False

    async def send_startup_notification(self) -> bool:
        """Send a notification that the monitor has started.

        Returns:
            True if sent, False on failure.
        """
        now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        message = (
            "🚀 <b>Instagram Monitor Started</b>\n"
            "\n"
            "The monitoring service is now running.\n"
            "Use /list to see monitored usernames.\n"
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
        """Send an error notification.

        Args:
            error: Error description.

        Returns:
            True if sent, False on failure.
        """
        now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        message = (
            "⚠️ <b>Monitor Error</b>\n"
            "\n"
            f"<code>{error[:500]}</code>\n"
            f"\n"
            f"🕐 {now}"
        )
        try:
            await self._send_message(message)
            return True
        except Exception as e:
            logger.error("Failed to send error alert: {}", e)
            return False

    # ── Lifecycle Methods ─────────────────────────────────────────────

    async def start(self) -> Application:
        """Build, initialize, and start the bot polling.

        Returns:
            The running Application instance.
        """
        logger.info("Starting Telegram bot")
        self._app = await self._build_app()
        self._bot = self._app.bot

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        logger.info("Telegram bot is now polling for commands")
        return self._app

    async def stop(self) -> None:
        """Stop the bot polling and shutdown."""
        if self._app:
            logger.info("Stopping Telegram bot")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")

"""Main entry point — wires all components and runs bot + monitor concurrently.

Design Decisions:
    - Telegram bot polling and monitor scheduler run as concurrent asyncio tasks.
    - The bot provides interactive control (/monitor, /demonitor, etc).
    - The scheduler runs periodic checks in the background.
    - Both share the same repository and checker instances.
    - Signal handlers trigger graceful shutdown of both.
"""

import asyncio
import signal
from types import FrameType
from typing import Optional

from loguru import logger

from app.checker.instagram import InstagramChecker
from app.config.settings import get_settings, Settings
from app.database.engine import DatabaseManager
from app.database.repository import UsernameRepository
from app.monitor.scheduler import MonitorScheduler
from app.notifier.telegram import TelegramBot
from app.utils.logging import setup_logging


async def run_service() -> None:
    """Initialize all components and run the monitoring service.

    Starts both:
    1. Telegram bot (polling for commands)
    2. Monitor scheduler (periodic checks)

    They run concurrently and share the same database + checker.
    """
    # ── Step 1: Configuration ──────────────────────────────
    settings: Settings = get_settings()

    # ── Step 2: Logging ────────────────────────────────────
    setup_logging(settings)
    logger.info("=" * 60)
    logger.info("Instagram Monitor v1.0.0 starting")
    logger.info("Environment: {}", settings.environment)
    logger.info("Check interval: {}s", settings.check_interval_seconds)
    logger.info("Max concurrent checks: {}", settings.max_concurrent_checks)
    logger.info("=" * 60)

    # ── Step 3: Database ───────────────────────────────────
    db_manager = DatabaseManager(settings.database_url)
    try:
        await db_manager.init_db()
        logger.info("Database connected and tables initialized")
    except Exception as e:
        logger.critical("Failed to initialize database: {}", e)
        raise SystemExit(1) from e

    repository = UsernameRepository(db_manager.session_factory)

    # ── Step 4: Services ───────────────────────────────────
    checker = InstagramChecker(
        check_delay=settings.check_delay_seconds,
    )

    # ── Step 5: Telegram Bot ───────────────────────────────
    telegram_bot = TelegramBot(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    telegram_bot.setup(repository=repository, checker=checker)

    # ── Step 6: Monitor Scheduler ──────────────────────────
    monitor = MonitorScheduler(
        repository=repository,
        checker=checker,
        notifier=telegram_bot,
        check_interval=settings.check_interval_seconds,
        max_concurrent=settings.max_concurrent_checks,
    )

    # ── Step 7: Start both concurrently ────────────────────
    async def _run_bot() -> None:
        """Start the Telegram bot polling."""
        await telegram_bot.start()
        await telegram_bot.send_startup_notification()
        logger.info("Telegram bot started and polling for commands")
        # Keep running until stopped
        while True:
            await asyncio.sleep(1)

    async def _run_monitor() -> None:
        """Start the monitoring scheduler."""
        # Small delay to let the bot start first
        await asyncio.sleep(2)
        await monitor.start()

    # Signal handling for graceful shutdown
    shutdown_event = asyncio.Event()

    def _handle_signal(sig: int, frame: Optional[FrameType] = None) -> None:
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        sig_name = signal.Signals(sig).name
        logger.info("Received {} — shutting down gracefully", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Run bot, monitor, and shutdown watcher concurrently
    bot_task = asyncio.create_task(_run_bot())
    monitor_task = asyncio.create_task(_run_monitor())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        # Wait for shutdown signal
        done, pending = await asyncio.wait(
            [bot_task, monitor_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If shutdown was triggered, stop everything
        logger.info("Initiating shutdown...")
        await monitor.stop()

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
        await monitor.stop()

    finally:
        # ── Cleanup ────────────────────────────────────────
        logger.info("Cleaning up resources...")

        # Cancel pending tasks
        bot_task.cancel()
        monitor_task.cancel()

        # Stop services
        await telegram_bot.stop()
        await checker.close()
        await db_manager.close()

        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(run_service())

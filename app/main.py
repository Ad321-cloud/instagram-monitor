"""Main entry point — wires all components together and starts the monitoring service.

Design Decisions:
    - Dependency injection: each component receives its dependencies explicitly.
      No hidden globals, no import-time side effects.
    - Signal handlers for graceful shutdown: SIGTERM/SIGINT trigger the stop event,
      allowing the current cycle to finish before exit.
    - Startup sequence: config → logging → database → checker → notifier → monitor.
      Each step validates the previous one succeeded before continuing.
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
from app.notifier.telegram import TelegramNotifier
from app.utils.logging import setup_logging


async def run_service() -> None:
    """Initialize all components and run the monitoring service.

    Orchestrates the full startup → run → shutdown lifecycle:
    1. Load configuration from .env
    2. Initialize logging
    3. Connect to database and create tables
    4. Initialize Instagram checker and Telegram notifier
    5. Start the monitoring loop
    6. Handle graceful shutdown on SIGTERM/SIGINT
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
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    # ── Step 5: Monitor ────────────────────────────────────
    monitor = MonitorScheduler(
        repository=repository,
        checker=checker,
        notifier=notifier,
        check_interval=settings.check_interval_seconds,
        max_concurrent=settings.max_concurrent_checks,
    )

    # ── Step 6: Signal handlers for graceful shutdown ──────
    def _handle_signal(sig: int, frame: Optional[FrameType] = None) -> None:
        """Handle shutdown signals (SIGTERM, SIGINT)."""
        sig_name = signal.Signals(sig).name
        logger.info("Received {} — shutting down gracefully", sig_name)
        asyncio.get_event_loop().create_task(monitor.stop())

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── Run the monitoring loop ────────────────────────────
    try:
        await monitor.start()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        # ── Cleanup ────────────────────────────────────────
        logger.info("Cleaning up resources...")
        await checker.close()
        await notifier.close()
        await db_manager.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(run_service())

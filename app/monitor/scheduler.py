"""Monitoring scheduler — orchestrates periodic username checks.

Design Decisions:
    - Pure asyncio loop — no external dependencies, runs in-process.
    - Graceful shutdown via asyncio.Event.
    - State change detection with follower count and disable/return timestamps
      passed through to the Telegram bot for rich notifications.
    - Each cycle is wrapped in try/except — a single failed check should never
      crash the entire monitoring loop.
"""

import asyncio
from typing import Optional

from loguru import logger

from app.checker.instagram import CheckResult, InstagramChecker
from app.database.repository import UsernameRepository
from app.models.username import UsernameStatus
from app.notifier.telegram import TelegramBot


class MonitorScheduler:
    """Orchestrates periodic Instagram username monitoring.

    Runs an async loop that:
    1. Fetches all active usernames from the database.
    2. Checks each username via the Instagram checker.
    3. Detects state changes by comparing with stored status.
    4. Records history and sends Telegram notifications on changes.
    """

    def __init__(
        self,
        repository: UsernameRepository,
        checker: InstagramChecker,
        notifier: TelegramBot,
        check_interval: int = 300,
        max_concurrent: int = 3,
    ) -> None:
        """Initialize the monitor scheduler.

        Args:
            repository: Database repository for username operations.
            checker: Instagram username checker instance.
            notifier: Telegram bot for notifications.
            check_interval: Seconds between monitoring cycles.
            max_concurrent: Maximum concurrent Instagram checks.
        """
        self._repository = repository
        self._checker = checker
        self._notifier = notifier
        self._check_interval = check_interval
        self._max_concurrent = max_concurrent
        self._stop_event = asyncio.Event()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Whether the monitoring loop is currently running."""
        return self._is_running

    async def _process_result(self, result: CheckResult) -> None:
        """Process a single check result: update DB and notify on state change.

        Args:
            result: The check result from the Instagram checker.
        """
        try:
            # Map string status to enum
            try:
                new_status = UsernameStatus(result.status)
            except ValueError:
                new_status = UsernameStatus.UNKNOWN

            # Update database — returns StatusHistory only if status changed
            history_entry = await self._repository.update_status(
                username=result.username,
                new_status=new_status,
                http_code=result.http_status_code,
                response_time=result.response_time_ms,
                follower_count=result.follower_count,
                error=result.error,
            )

            # Send notification if status actually changed
            if history_entry is not None:
                old_status = history_entry.old_status or "unknown"

                # Get the updated DB record for disable/return timestamps
                db_record = await self._repository.get_by_username(result.username)

                await self._notifier.send_status_change(
                    username=result.username,
                    old_status=old_status,
                    new_status=result.status,
                    follower_count=result.follower_count,
                    disabled_at=db_record.disabled_at if db_record else None,
                    returned_at=db_record.returned_at if db_record else None,
                )

        except Exception as e:
            logger.error(
                "Error processing result for {}: {}",
                result.username, e,
            )
            await self._notifier.send_error_alert(
                f"Error processing {result.username}: {e}"
            )

    async def _run_cycle(self) -> None:
        """Execute a single monitoring cycle."""
        logger.info("Starting monitoring cycle")

        active_usernames = await self._repository.get_all_active()

        if not active_usernames:
            logger.info("No active usernames to monitor")
            return

        username_list = [u.username for u in active_usernames]
        logger.info("Checking {} usernames: {}", len(username_list), username_list)

        results = await self._checker.check_many(
            usernames=username_list,
            max_concurrent=self._max_concurrent,
        )

        for result in results:
            await self._process_result(result)

        # Summary log
        status_counts: dict[str, int] = {}
        for r in results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        logger.info("Cycle complete | Results: {}", status_counts)

    async def start(self) -> None:
        """Start the monitoring loop.

        Runs indefinitely until stop() is called.
        """
        if self._is_running:
            logger.warning("Monitor is already running")
            return

        self._is_running = True
        self._stop_event.clear()
        logger.info(
            "Monitor started | interval={}s | max_concurrent={}",
            self._check_interval, self._max_concurrent,
        )

        while not self._stop_event.is_set():
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error("Monitoring cycle failed: {}", e)
                await self._notifier.send_error_alert(f"Cycle failed: {e}")

            # Wait for the interval or until stop is requested
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._check_interval,
                )
            except asyncio.TimeoutError:
                pass  # Normal — time for next cycle

        self._is_running = False
        logger.info("Monitor stopped")

    async def stop(self) -> None:
        """Signal the monitoring loop to stop gracefully."""
        logger.info("Stop signal received")
        self._stop_event.set()

    async def run_once(self) -> None:
        """Run a single monitoring cycle (for testing/CLI)."""
        logger.info("Running single monitoring cycle")
        await self._run_cycle()

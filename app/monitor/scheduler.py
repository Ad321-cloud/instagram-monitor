"""Monitoring scheduler — orchestrates periodic username checks.

Design Decisions:
    - Pure asyncio loop instead of APScheduler/Celery — no external dependencies,
      runs in-process, trivially testable. Sufficient for single-server deployment.
    - Graceful shutdown via asyncio.Event — the monitor can be stopped cleanly from
      anywhere (SIGTERM handler, CLI command, etc).
    - State change detection happens HERE, not in the checker — separation of concerns.
      The checker just returns HTTP status; the monitor decides what to do with it.
    - Each cycle is wrapped in try/except — a single failed check should never crash
      the entire monitoring loop.
"""

import asyncio
from typing import Optional

from loguru import logger

from app.checker.instagram import CheckResult, InstagramChecker
from app.database.repository import UsernameRepository
from app.models.username import UsernameStatus
from app.notifier.telegram import TelegramNotifier


class MonitorScheduler:
    """Orchestrates periodic Instagram username monitoring.

    Runs an async loop that:
    1. Fetches all active usernames from the database.
    2. Checks each username via the Instagram checker.
    3. Detects state changes by comparing with stored status.
    4. Records history and sends Telegram notifications on changes.

    Attributes:
        _repository: Database repository for username operations.
        _checker: Instagram profile status checker.
        _notifier: Telegram notification sender.
        _check_interval: Seconds between monitoring cycles.
        _max_concurrent: Max parallel checks per cycle.
        _stop_event: Signal to gracefully stop the monitoring loop.
        _is_running: Whether the monitor is currently active.
    """

    def __init__(
        self,
        repository: UsernameRepository,
        checker: InstagramChecker,
        notifier: TelegramNotifier,
        check_interval: int = 300,
        max_concurrent: int = 3,
    ) -> None:
        """Initialize the monitor scheduler.

        Args:
            repository: Database repository for username CRUD and status tracking.
            checker: Instagram username checker instance.
            notifier: Telegram notifier for alerts.
            check_interval: Seconds between complete monitoring cycles.
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
                error=result.error,
            )

            # Send notification if status actually changed
            if history_entry is not None:
                old_status = history_entry.old_status or "unknown"
                await self._notifier.send_status_change(
                    username=result.username,
                    old_status=old_status,
                    new_status=result.status,
                )

        except Exception as e:
            logger.error(
                "Error processing result for {}: {}",
                result.username,
                e,
            )
            # Don't crash the loop — log and continue
            await self._notifier.send_error_alert(
                f"Error processing {result.username}: {e}"
            )

    async def _run_cycle(self) -> None:
        """Execute a single monitoring cycle.

        Fetches all active usernames, checks them, and processes results.
        """
        logger.info("Starting monitoring cycle")

        # Get all active usernames from the database
        active_usernames = await self._repository.get_all_active()

        if not active_usernames:
            logger.info("No active usernames to monitor")
            return

        username_list = [u.username for u in active_usernames]
        logger.info("Checking {} usernames: {}", len(username_list), username_list)

        # Check all usernames with concurrency control
        results = await self._checker.check_many(
            usernames=username_list,
            max_concurrent=self._max_concurrent,
        )

        # Process each result (update DB, notify on changes)
        for result in results:
            await self._process_result(result)

        # Summary log
        status_counts: dict[str, int] = {}
        for r in results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        logger.info("Cycle complete | Results: {}", status_counts)

    async def start(self) -> None:
        """Start the monitoring loop.

        Runs indefinitely until stop() is called. Each cycle:
        1. Runs all checks.
        2. Waits for check_interval seconds (or until stopped).

        The loop is resilient — a failed cycle logs the error and continues.
        """
        if self._is_running:
            logger.warning("Monitor is already running")
            return

        self._is_running = True
        self._stop_event.clear()
        logger.info(
            "Monitor started | interval={}s | max_concurrent={}",
            self._check_interval,
            self._max_concurrent,
        )

        await self._notifier.send_startup_notification()

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
                # Normal — timeout means it's time for the next cycle
                pass

        self._is_running = False
        logger.info("Monitor stopped")

    async def stop(self) -> None:
        """Signal the monitoring loop to stop gracefully.

        The current cycle will complete before the loop exits.
        """
        logger.info("Stop signal received")
        self._stop_event.set()

    async def run_once(self) -> None:
        """Run a single monitoring cycle (useful for testing/CLI).

        Does not start the loop — just runs one check cycle and returns.
        """
        logger.info("Running single monitoring cycle")
        await self._run_cycle()

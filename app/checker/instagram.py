"""Async Instagram profile status checker.

Design Decisions:
    - aiohttp over httpx: lower overhead for simple GET requests, better async perf.
    - User-Agent rotation: Instagram fingerprints request patterns. Rotating among
      realistic browser UAs reduces detection probability.
    - Jittered delays: constant intervals create detectable patterns. ±30% jitter
      makes request timing look organic.
    - Semaphore-based concurrency: limits parallel requests to avoid overwhelming
      Instagram's rate limiter. Default max_concurrent=3 is conservative.
    - tenacity retries only on network errors: retrying a 404 or 200 is meaningless.
      We only retry when the request itself failed (timeout, connection error).
    - allow_redirects=False: Instagram redirects to /accounts/login/ for unavailable
      profiles. We need to detect the redirect, not follow it.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Realistic browser User-Agent strings for rotation
_USER_AGENTS: list[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


@dataclass
class CheckResult:
    """Result of checking a single Instagram username.

    Attributes:
        username: The Instagram username that was checked.
        status: Detected status ('active', 'available', 'unavailable', 'unknown').
        http_status_code: HTTP response code (None if request failed entirely).
        response_time_ms: Round-trip time in milliseconds.
        error: Error message if the check failed.
        checked_at: UTC timestamp of when the check was performed.
    """

    username: str
    status: str
    http_status_code: Optional[int] = None
    response_time_ms: float = 0.0
    error: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InstagramChecker:
    """Async Instagram username availability checker.

    Uses HTTP requests to Instagram's public profile URLs to determine
    username state. Implements rate limiting, retry logic, and
    concurrent check management.

    Usage:
        async with InstagramChecker(check_delay=10.0) as checker:
            result = await checker.check_username("target_user")
            results = await checker.check_many(["user1", "user2"], max_concurrent=3)
    """

    def __init__(self, check_delay: float = 10.0) -> None:
        """Initialize the checker.

        Args:
            check_delay: Base delay in seconds between consecutive checks.
                         Actual delay is jittered ±30% to avoid pattern detection.
        """
        self._check_delay: float = check_delay
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp client session.

        Lazily initializes the session with a 15-second timeout and
        connection pooling. Using a single session across checks enables
        HTTP connection reuse.

        Returns:
            The aiohttp ClientSession instance.
        """
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )
        return self._session

    def _get_headers(self) -> dict[str, str]:
        """Generate realistic browser request headers with a rotated User-Agent.

        Returns:
            Dictionary of HTTP headers mimicking a real browser.
        """
        return {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }

    def _interpret_status(self, http_status: int) -> str:
        """Interpret an HTTP status code into a username state.

        Args:
            http_status: The HTTP response status code.

        Returns:
            One of: 'active', 'available', 'unavailable', 'unknown'.
        """
        if http_status == 200:
            return "active"
        elif http_status == 404:
            return "available"
        elif http_status in (301, 302, 303, 307, 308):
            # Redirects typically go to /accounts/login/ for suspended/private profiles
            return "unavailable"
        elif http_status == 429:
            return "unknown"  # Rate limited
        else:
            return "unknown"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _do_request(self, url: str) -> tuple[int, float]:
        """Perform the actual HTTP request with retry logic.

        Only retries on network/timeout errors — NOT on valid HTTP responses
        like 404 or 200, which are meaningful results.

        Args:
            url: The Instagram profile URL to request.

        Returns:
            Tuple of (http_status_code, response_time_ms).

        Raises:
            aiohttp.ClientError: After all retry attempts exhausted.
            asyncio.TimeoutError: After all retry attempts exhausted.
        """
        session = await self._get_session()
        start_time = time.monotonic()

        async with session.get(
            url,
            headers=self._get_headers(),
            allow_redirects=False,  # We need to detect redirects, not follow them
        ) as response:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return response.status, elapsed_ms

    async def check_username(self, username: str) -> CheckResult:
        """Check the status of a single Instagram username.

        Sends a GET request to the public profile URL and interprets
        the HTTP response to determine username state.

        Args:
            username: Instagram username to check (without @).

        Returns:
            CheckResult with the detected status and metadata.
        """
        url = f"https://www.instagram.com/{username.lower().strip()}/"
        logger.debug("Checking username: {} -> {}", username, url)

        try:
            http_status, response_time = await self._do_request(url)
            status = self._interpret_status(http_status)

            if http_status == 429:
                logger.warning(
                    "Rate limited while checking {}: HTTP 429", username
                )
            else:
                logger.info(
                    "Check result: {} -> {} (HTTP {}, {:.0f}ms)",
                    username,
                    status,
                    http_status,
                    response_time,
                )

            return CheckResult(
                username=username,
                status=status,
                http_status_code=http_status,
                response_time_ms=response_time,
            )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("Check failed for {}: {}", username, error_msg)
            return CheckResult(
                username=username,
                status="unknown",
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {type(e).__name__}: {e}"
            logger.error("Unexpected check failure for {}: {}", username, error_msg)
            return CheckResult(
                username=username,
                status="unknown",
                error=error_msg,
            )

    def _jittered_delay(self) -> float:
        """Calculate a delay with ±30% random jitter.

        Jitter prevents request patterns that Instagram could detect
        and use for rate limiting.

        Returns:
            The jittered delay in seconds.
        """
        jitter_factor = 1.0 + random.uniform(-0.3, 0.3)
        return self._check_delay * jitter_factor

    async def check_many(
        self,
        usernames: list[str],
        max_concurrent: int = 3,
    ) -> list[CheckResult]:
        """Check multiple usernames with concurrency control and delays.

        Uses an asyncio.Semaphore to limit parallel requests and adds
        jittered delays between checks to avoid rate limiting.

        Args:
            usernames: List of Instagram usernames to check.
            max_concurrent: Maximum number of concurrent checks.

        Returns:
            List of CheckResult objects, one per username.
        """
        if not usernames:
            return []

        logger.info(
            "Checking {} usernames (max_concurrent={})",
            len(usernames),
            max_concurrent,
        )

        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[CheckResult] = []

        async def _check_with_limit(username: str) -> CheckResult:
            async with semaphore:
                result = await self.check_username(username)
                # Add jittered delay between checks
                delay = self._jittered_delay()
                logger.debug("Waiting {:.1f}s before next check", delay)
                await asyncio.sleep(delay)
                return result

        tasks = [_check_with_limit(u) for u in usernames]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        logger.info(
            "Completed checking {} usernames",
            len(results),
        )
        return results

    async def close(self) -> None:
        """Close the aiohttp session and free resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Instagram checker session closed")

    async def __aenter__(self) -> "InstagramChecker":
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager and close resources."""
        await self.close()

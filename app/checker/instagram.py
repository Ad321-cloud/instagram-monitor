"""Async Instagram profile status checker with follower count extraction.

Design Decisions:
    - aiohttp over httpx: lower overhead for simple GET requests, better async perf.
    - User-Agent rotation: Instagram fingerprints request patterns. Rotating among
      realistic browser UAs reduces detection probability.
    - Jittered delays: constant intervals create detectable patterns. ±30% jitter
      makes request timing look organic.
    - Semaphore-based concurrency: limits parallel requests to avoid overwhelming
      Instagram's rate limiter. Default max_concurrent=3 is conservative.
    - tenacity retries only on network errors: retrying a 404 or 200 is meaningless.
    - Follower count extraction: parsed from og:description meta tag on 200 responses.
      Falls back to None if parsing fails — follower count is a bonus, not critical.
    - 20-second timeout: user-specified, accounts for Instagram's variable response times.
"""

import asyncio
import random
import re
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
]

# Regex to extract follower count from Instagram's og:description meta tag
# Format: "1,234 Followers" or "12.3K Followers" or "1.5M Followers"
_FOLLOWER_REGEX = re.compile(
    r'<meta\s+(?:property=["\']og:description["\']\s+content=["\']|content=["\'])'
    r'([\d,.]+[KMB]?)\s+Followers',
    re.IGNORECASE,
)

# Alternative: try to find in JSON data embedded in the page
_FOLLOWER_JSON_REGEX = re.compile(
    r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
)


def _parse_follower_string(follower_str: str) -> Optional[int]:
    """Parse a follower count string into an integer.

    Handles formats like: "1,234", "12.3K", "1.5M", "2B"

    Args:
        follower_str: Raw follower count string from Instagram.

    Returns:
        Integer follower count, or None if parsing fails.
    """
    try:
        # Remove commas
        clean = follower_str.strip().replace(",", "")

        # Handle K/M/B suffixes
        if clean.upper().endswith("K"):
            return int(float(clean[:-1]) * 1_000)
        elif clean.upper().endswith("M"):
            return int(float(clean[:-1]) * 1_000_000)
        elif clean.upper().endswith("B"):
            return int(float(clean[:-1]) * 1_000_000_000)
        else:
            return int(clean)
    except (ValueError, TypeError):
        return None


@dataclass
class CheckResult:
    """Result of checking a single Instagram username.

    Attributes:
        username: The Instagram username that was checked.
        status: Detected status ('active', 'available', 'unavailable', 'unknown').
        http_status_code: HTTP response code (None if request failed entirely).
        response_time_ms: Round-trip time in milliseconds.
        follower_count: Follower count if extracted from profile (None if unavailable).
        error: Error message if the check failed.
        checked_at: UTC timestamp of when the check was performed.
    """

    username: str
    status: str
    http_status_code: Optional[int] = None
    response_time_ms: float = 0.0
    follower_count: Optional[int] = None
    error: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InstagramChecker:
    """Async Instagram username status checker with follower count extraction.

    Uses HTTP requests to Instagram's public profile URLs to determine
    username state and extract follower count. Implements rate limiting,
    retry logic, and concurrent check management.

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

        Returns:
            The aiohttp ClientSession instance.
        """
        if self._session is None or self._session.closed:
            # 20-second timeout as specified
            timeout = aiohttp.ClientTimeout(total=20)
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
            # Redirects are commonly Instagram login/challenge/rate-limit pages.
            # They do not prove that the profile is disabled.
            return "unknown"
        elif http_status == 429:
            return "unknown"  # Rate limited
        else:
            return "unknown"

    def _extract_follower_count(self, html: str) -> Optional[int]:
        """Extract follower count from Instagram profile HTML.

        Tries two methods:
        1. og:description meta tag (e.g., "1,234 Followers, ...")
        2. JSON data embedded in the page (edge_followed_by.count)

        Args:
            html: Raw HTML response body.

        Returns:
            Integer follower count, or None if extraction fails.
        """
        # Method 1: og:description meta tag
        match = _FOLLOWER_REGEX.search(html)
        if match:
            count = _parse_follower_string(match.group(1))
            if count is not None:
                return count

        # Method 2: JSON embedded data
        match = _FOLLOWER_JSON_REGEX.search(html)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _do_request(self, url: str) -> tuple[int, float, Optional[str]]:
        """Perform the actual HTTP request with retry logic.

        Only retries on network/timeout errors — NOT on valid HTTP responses.
        For 200 responses, reads the body to extract follower count.

        Args:
            url: The Instagram profile URL to request.

        Returns:
            Tuple of (http_status_code, response_time_ms, body_text_or_none).

        Raises:
            aiohttp.ClientError: After all retry attempts exhausted.
            asyncio.TimeoutError: After all retry attempts exhausted.
        """
        session = await self._get_session()
        start_time = time.monotonic()

        async with session.get(
            url,
            headers=self._get_headers(),
            allow_redirects=False,  # Detect redirects, don't follow them
        ) as response:
            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Only read body for 200 responses (to extract follower count)
            body: Optional[str] = None
            if response.status == 200:
                try:
                    body = await response.text()
                except Exception:
                    pass  # Body read failed — not critical

            return response.status, elapsed_ms, body

    async def check_username(self, username: str) -> CheckResult:
        """Check the status of a single Instagram username.

        Sends a GET request to the public profile URL, interprets
        the HTTP response, and extracts follower count for active profiles.

        Args:
            username: Instagram username to check (without @).

        Returns:
            CheckResult with status, follower count, and metadata.
        """
        clean_username = username.lower().strip().lstrip("@")
        url = f"https://www.instagram.com/{clean_username}/"
        logger.debug("Checking username: {} -> {}", clean_username, url)

        try:
            http_status, response_time, body = await self._do_request(url)
            status = self._interpret_status(http_status)
            
            # Extract follower count for active profiles
            follower_count: Optional[int] = None
            if status == "active" and body:
                follower_count = self._extract_follower_count(body)
                if follower_count is not None:
                    logger.info(
                        "Extracted follower count for {}: {}",
                        clean_username,
                        follower_count,
                    )

            if http_status == 429:
                logger.warning(
                    "Rate limited while checking {}: HTTP 429", clean_username
                )
            else:
                logger.info(
                    "Check result: {} -> {} (HTTP {}, {:.0f}ms, followers={})",
                    clean_username,
                    status,
                    http_status,
                    response_time,
                    follower_count,
                )

            return CheckResult(
                username=clean_username,
                status=status,
                http_status_code=http_status,
                response_time_ms=response_time,
                follower_count=follower_count,
            )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("Check failed for {}: {}", clean_username, error_msg)
            return CheckResult(
                username=clean_username,
                status="unknown",
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {type(e).__name__}: {e}"
            logger.error("Unexpected check failure for {}: {}", clean_username, error_msg)
            return CheckResult(
                username=clean_username,
                status="unknown",
                error=error_msg,
            )

    def _jittered_delay(self) -> float:
        """Calculate a delay with ±30% random jitter.

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
                delay = self._jittered_delay()
                logger.debug("Waiting {:.1f}s before next check", delay)
                await asyncio.sleep(delay)
                return result

        tasks = [_check_with_limit(u) for u in usernames]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        logger.info("Completed checking {} usernames", len(results))
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

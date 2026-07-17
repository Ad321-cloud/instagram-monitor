"""Logging configuration using Loguru.

Design Decisions:
    - Loguru over stdlib logging: zero-config structured logging with rotation,
      compression, and colored output. Eliminates ~40 lines of handler boilerplate.
    - Three handlers: console (human-readable), file (debug-level archive),
      error file (ERROR+ only for quick incident triage).
    - get_logger() returns the loguru singleton — no need to pass logger instances
      around. Modules just call `from app.utils.logging import get_logger`.
"""

import os
import sys
from typing import Any

from loguru import logger

from app.config.settings import Settings


def setup_logging(settings: Settings) -> Any:
    """Configure Loguru with console, file, and error handlers.

    Removes the default stderr handler and installs three custom handlers:
    1. Console: colored output at the configured log level.
    2. File: rotating debug-level logs (10MB, 7-day retention, gzip compressed).
    3. Error file: ERROR+ only, for fast incident investigation.

    Args:
        settings: Application Settings with log_level, log_dir, and environment.

    Returns:
        The configured loguru logger instance.
    """
    # Remove default handler to prevent duplicate output
    logger.remove()

    # Ensure log directory exists
    os.makedirs(settings.log_dir, exist_ok=True)

    # --- Console handler (colored, configurable level) ---
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # --- File handler (all logs, rotating, compressed) ---
    logger.add(
        os.path.join(settings.log_dir, "monitor.log"),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )

    # --- Error-only file handler (for quick triage) ---
    logger.add(
        os.path.join(settings.log_dir, "errors.log"),
        level="ERROR",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} - {message}\n{exception}"
        ),
    )

    logger.info(
        "Logging initialized | level={} | env={} | dir={}",
        settings.log_level,
        settings.environment,
        settings.log_dir,
    )
    return logger


def get_logger() -> Any:
    """Get the Loguru logger instance.

    This is a convenience function. Loguru uses a global singleton,
    so this always returns the same pre-configured logger.

    Returns:
        The loguru logger instance.
    """
    return logger

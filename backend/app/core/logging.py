"""Unified logging configuration for AI Writer backend.

Usage:
    from app.core import get_logger, setup_logging

    # Setup logging at application start
    setup_logging(level="INFO", log_dir=Path("logs"))

    # Get a logger for a module
    logger = get_logger(__name__)
    logger.info("Message", extra={"book_id": "abc123"})
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    *,
    level: str = "INFO",
    log_dir: Optional[Path] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files. If None, only console logging.
        max_bytes: Max size per log file before rotation
        backup_count: Number of backup files to keep
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root_logger.addHandler(console_handler)

    # File handler (with rotation)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")
        log_file = log_dir / f"aiwriter_{timestamp}.log"

        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module.

    Args:
        name: Usually __name__ of the calling module

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for timing operations.

    Usage:
        with LogContext(logger, "chapter_generation", chapter_id=1):
            # ... operation ...
    """

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        **extra: str,
    ) -> None:
        self.logger = logger
        self.operation = operation
        self.extra = extra
        self.start_time: Optional[float] = None

    def __enter__(self) -> "LogContext":
        self.start_time = datetime.now().timestamp()
        self.logger.debug(
            f"Starting {self.operation}",
            extra=self.extra,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = datetime.now().timestamp() - (self.start_time or 0)
        if exc_type:
            self.logger.error(
                f"Failed {self.operation} after {elapsed:.2f}s: {exc_val}",
                extra={**self.extra, "elapsed_seconds": f"{elapsed:.2f}"},
            )
        else:
            self.logger.info(
                f"Completed {self.operation} in {elapsed:.2f}s",
                extra={**self.extra, "elapsed_seconds": f"{elapsed:.2f}"},
            )

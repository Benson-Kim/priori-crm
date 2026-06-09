"""Centralized logging configuration with structured output."""

import logging
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

from app.lib.config import settings


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with additional context fields."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Add custom fields to log records."""
        super().add_fields(log_record, record, message_dict)
        log_record["environment"] = settings.ENVIRONMENT
        log_record["app_name"] = settings.APP_NAME
        log_record["app_version"] = settings.APP_VERSION


def setup_logging() -> None:
    """Configure application-wide logging."""
    log_level = getattr(logging, settings.LOG_LEVEL)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # Use JSON formatting in production, human-readable in development
    if settings.is_production:
        formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s",
            rename_fields={"levelname": "level", "asctime": "timestamp"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Silence noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given module."""
    return logging.getLogger(name)

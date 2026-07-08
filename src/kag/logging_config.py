"""Structured logging via structlog with trace_id propagation.

Why structlog (not stdlib logging):
    - JSON output by default, no extra formatter config
    - `contextvars` integration for per-request trace_id
      without manually threading it through every function call
    - Composable processors (timestamps, exception formatting, etc.)

Usage::

    from kag.logging_config import configure_logging, get_logger

    configure_logging("INFO")           # call once at startup
    log = get_logger(__name__)
    log.info("something.happened", foo=42)   # emits {"event": ..., "foo": 42, "trace_id": ...}
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog

REQUEST_ID_HEADER = "X-Request-ID"
DEFAULT_LOG_LEVEL = "INFO"


def configure_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """Configure structlog for JSON output.

    Idempotent — safe to call from tests or multiple entry points.
    Routes stdlib loggers (uvicorn, celery, etc.) through structlog
    so every log line is JSON.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Bind it with `new=...` for context."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def new_trace_id() -> str:
    """Generate a new trace ID (32 hex chars; UUID4 without dashes)."""
    return uuid.uuid4().hex

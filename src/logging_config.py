"""Structured logging configuration for Believable Minds.

Provides JSON-formatted log output with contextual fields (video_id,
person_name, provider, etc.) for observability in production.
Falls back to human-readable format in development.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """JSON log formatter that includes extra context fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include any extra context passed via logger.info("msg", extra={...})
        # or via the `ctx` dict pattern
        for key in ("video_id", "person_name", "provider", "channel_name",
                     "stage", "claim_count", "duration_ms", "error_type",
                     "model", "tokens_used", "batch_index"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
            log_entry["exception_type"] = type(record.exc_info[1]).__name__

        return json.dumps(log_entry, default=str)


class ReadableFormatter(logging.Formatter):
    """Human-readable formatter for development with context fields."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        # Append any structured context fields
        extras = []
        for key in ("video_id", "person_name", "provider", "stage",
                     "claim_count", "model", "tokens_used"):
            value = getattr(record, key, None)
            if value is not None:
                extras.append(f"{key}={value}")
        if extras:
            return f"{base} [{', '.join(extras)}]"
        return base


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application.

    Uses JSON format in production (LOG_FORMAT=json or RAILWAY_ENVIRONMENT set),
    human-readable format in development.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    is_production = (
        os.environ.get("LOG_FORMAT", "").lower() == "json"
        or os.environ.get("RAILWAY_ENVIRONMENT")
    )

    if is_production:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(ReadableFormatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))

    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

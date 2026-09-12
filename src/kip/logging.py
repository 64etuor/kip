from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Send KIP's own records to stderr as JSON.

    Only the `kip` logger is configured: an application that embeds this API
    keeps the root handlers it installed, and the CLI keeps stdout free for
    the envelope it prints.
    """
    logger = logging.getLogger("kip")
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    # The connection pool retries in the background and logs every failed
    # attempt at WARNING; the typed dependency_unavailable error already tells
    # the operator what to do, so keep the driver chatter out of CLI output.
    pool_logger = logging.getLogger("psycopg.pool")
    if pool_logger.level == logging.NOTSET:
        pool_logger.setLevel(logging.ERROR)

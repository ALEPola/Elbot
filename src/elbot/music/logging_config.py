"""Simple JSON logging configuration for music subsystem."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict

__all__ = ["configure_json_logging"]


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting only
        payload: Dict[str, Any] = {
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.args and isinstance(record.args, dict):
            payload.update(record.args)
        if record.__dict__.get("extra"):
            extra = record.__dict__["extra"]
            if isinstance(extra, dict):
                payload.update(extra)
        return json.dumps(payload, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])


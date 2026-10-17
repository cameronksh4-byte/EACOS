"""Structured JSON logging with PII redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from triage_engine.security.pii import redact_for_logs

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}


class PIIRedactionFilter(logging.Filter):
    """Redacts CURPs, personal RFCs and licence numbers from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_for_logs(record.getMessage())
        record.args = None
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line, ready for CloudWatch Logs Insights."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: messages and tracebacks can carry document data.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter and PII filter on the root logger."""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    handler.addFilter(PIIRedactionFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

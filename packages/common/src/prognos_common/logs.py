"""Structured logging for every Prognos service.

    from prognos_common.logs import configure
    configure("detector")

One JSON object per line (ts, level, service, logger, msg, + context such as
request_id and any `extra=` fields), which Loki/ELK/CloudWatch can index without
parsing rules. On an interactive terminal the format is human-readable text
instead; LOG_FORMAT=json|text overrides the choice.

`request_id` is carried in a context variable, so the API sets it once per request
and every log line written while serving that request carries it (correlation
across the request's log lines and its audit_log row).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
from typing import Any

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed via `extra=` and is kept.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            entry["request_id"] = rid
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure(service: str, level: str | None = None, fmt: str | None = None) -> None:
    """Configure the root logger once per process (idempotent)."""
    fmt = fmt or os.getenv("LOG_FORMAT") or ("text" if sys.stderr.isatty() else "json")
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(logging.Formatter(f"%(asctime)s %(levelname)s {service} %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.getenv("LOG_LEVEL") or "INFO")
    # Access logs from libraries are noise next to our own request metrics.
    logging.getLogger("httpx").setLevel(logging.WARNING)

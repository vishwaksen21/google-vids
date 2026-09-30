"""Structured JSON logging with secret redaction.

Every log line is one JSON object. Context (job id, stage, scene) is carried in a
contextvar so a worker step does not have to thread it through every call.

Redaction is applied to both keys and values: a field whose *name* looks sensitive is
masked, and any value that contains a bearer token, API key parameter, cookie header or
database password is scrubbed. Logs never contain Google session material because the
browser adapter never reads it, but the scrubber is the second line of defence.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "vidsprod_log_context", default=None
)

_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|session|credential)",
    re.IGNORECASE,
)
_VALUE_PATTERNS = (
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-~+/]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([?&](key|api_key|token)=)[^&\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(cookie[\"']?\s*[:=]\s*[\"']?)[^\"'\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+(@)"), r"\1[REDACTED]\2"),
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SENSITIVE_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        for pattern, replacement in _VALUE_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    return value


def bind(**fields: Any) -> contextvars.Token:
    """Add fields to the logging context for the current task."""
    merged = {**(_context.get() or {}), **{k: v for k, v in fields.items() if v is not None}}
    return _context.set(merged)


def reset(token: contextvars.Token) -> None:
    _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **(_context.get() or {}),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


class _Adapter(logging.LoggerAdapter):
    """`log.info("msg", key=value)` style, carried in `record.fields`."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        fields = {k: kwargs.pop(k) for k in list(kwargs) if k not in ("exc_info", "stack_info")}
        kwargs["extra"] = {"fields": fields}
        return msg, kwargs


def get_logger(name: str) -> _Adapter:
    return _Adapter(logging.getLogger(name), {})


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Library chatter that would otherwise print request URLs or SQL parameters.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

"""Structured JSON logging, secret redaction and error classification.

The observability layer is deliberately dependency-free (stdlib ``logging``)
and must never leak secrets: every free-form payload that is logged goes
through :func:`redact` and, where it may be large (tool/LLM payloads), through
:func:`summarize`.

This module never raises into the pipeline: logging failures are swallowed by
the callers that use it from hot paths.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.config import settings

# Substrings that mark a dict key (case-insensitive) as secret-bearing.
_SECRET_KEYS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "authorization",
    "auth_header",
    "private_key",
)

# Free-form ``key=value`` / ``key: value`` secret patterns inside plain strings.
_SECRET_INLINE_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|authorization)\b\s*[:=]\s*['\"]?([^\s'\",}]+)"
)

REDACTED = "***REDACTED***"

_MAX_STRING = 500
_MAX_ITEMS = 5
_MAX_DEPTH = 5

_JSON_ATTRS = (
    "request_id",
    "case_id",
    "run_id",
    "node",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_category",
    "agent",
    "tool_name",
    "provider",
    "model",
    "hits",
    "vector_hits",
    "bm25_hits",
    "hybrid_hits",
)


def new_request_id() -> str:
    """Return a short opaque id used to correlate one request/run."""
    return uuid.uuid4().hex[:16]


def _redact_string(text: str) -> str:
    return _SECRET_INLINE_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)


def redact(value: Any) -> Any:
    """Recursively mask secret-looking keys/values in dicts, lists and strings."""
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and any(s in key.lower() for s in _SECRET_KEYS):
                cleaned[key] = REDACTED
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def truncate(value: Any, *, max_string: int = _MAX_STRING, max_items: int = _MAX_ITEMS,
             depth: int = 0) -> Any:
    """Bound a payload's size so audit logs stay readable and cheap to store."""
    if depth > _MAX_DEPTH:
        return "..."
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "…"
    if isinstance(value, dict):
        return {k: truncate(v, max_string=max_string, max_items=max_items, depth=depth + 1)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [truncate(v, max_string=max_string, max_items=max_items, depth=depth + 1)
                 for v in list(value)[:max_items]]
        if len(value) > max_items:
            items.append(f"...+{len(value) - max_items} more")
        return items
    return value


def _to_plain(value: Any) -> Any:
    """Convert pydantic models / dataclasses / mappings to JSON-friendly data."""
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - fall back to a safe repr
            return str(value)
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return value


def summarize(value: Any) -> Any:
    """Redact + truncate an arbitrary payload for safe auditing."""
    return truncate(redact(_to_plain(value)))


_ERROR_RULES: tuple[tuple[tuple[type[BaseException], ...], tuple[str, ...], str], ...] = (
    ((TimeoutError,), ("timeout",), "timeout"),
    ((ConnectionError,), ("connection", "socket", "unreachable"), "connectivity"),
    ((PermissionError,), ("auth", "forbidden", "unauthorized"), "authorization"),
    ((ImportError, ModuleNotFoundError), (), "dependency"),
    ((ValueError, TypeError, KeyError, AssertionError), (), "validation"),
)


def error_category(exc: BaseException) -> str:
    """Map an exception to a coarse, log-friendly category."""
    name = type(exc).__name__.lower()
    for types, markers, category in _ERROR_RULES:
        if isinstance(exc, types) or any(marker in name for marker in markers):
            return category
    return "internal"


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON with redacted structured fields."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib name
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        for attr in _JSON_ATTRS:
            if hasattr(record, attr):
                payload[attr] = getattr(record, attr)
        fields = getattr(record, "fields", None)
        if fields:
            payload["fields"] = redact(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


_logger = logging.getLogger("procurement.observability")
_configured = False


def get_logger(name: str = "procurement.observability") -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: str | None = None) -> None:
    """Install the JSON handler on the root logger exactly once."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._pv_json = True  # type: ignore[attr-defined]
    root = logging.getLogger()
    if not any(getattr(h, "_pv_json", False) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel((level or settings.log_level or "INFO").upper())
    _configured = True


def emit(event: str, message: str = "", *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured event; ``fields`` are redacted before logging."""
    extras: dict[str, Any] = {"event": event}
    for attr in _JSON_ATTRS:
        if attr in fields:
            extras[attr] = fields.pop(attr)
    extras["fields"] = redact(fields)
    try:
        _logger.log(level, message or event, extra=extras)
    except Exception:  # noqa: BLE001 - logging must never break the pipeline
        pass

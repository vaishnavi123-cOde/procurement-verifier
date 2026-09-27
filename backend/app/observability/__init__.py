"""Observability layer: structured JSON logging, redaction, request middleware."""

from backend.app.observability.logger import (
    configure_logging,
    emit,
    error_category,
    get_logger,
    new_request_id,
    redact,
    summarize,
)
from backend.app.observability.middleware import REQUEST_ID_HEADER, ObservabilityMiddleware

__all__ = [
    "configure_logging",
    "emit",
    "error_category",
    "get_logger",
    "new_request_id",
    "redact",
    "summarize",
    "ObservabilityMiddleware",
    "REQUEST_ID_HEADER",
]

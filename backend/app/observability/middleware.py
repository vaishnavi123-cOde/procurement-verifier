"""Starlette/FastAPI middleware that correlates and times every request."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.app.observability.logger import emit, new_request_id

REQUEST_ID_HEADER = "X-Request-ID"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Attach/propagate ``X-Request-ID`` and log request timing.

    The request id is also stored on ``request.state.request_id`` so route
    handlers and background work can correlate their own logs/records.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        request.state.request_id = request_id
        started = time.monotonic()
        status_code = 500
        error_type: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception as exc:  # noqa: BLE001 - log then let the app handle it
            error_type = type(exc).__name__
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            fields = {"error_type": error_type} if error_type else {}
            emit(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                request_id=request_id,
                level=logging.ERROR if status_code >= 500 else logging.INFO,
                **fields,
            )

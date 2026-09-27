"""Lightweight API security: token auth, auth dependency, rate limiting.

Design constraints (Phase 17):
- Local development must stay one-step: auth is OFF by default
  (``AUTH_ENABLED=false``) and every API route still works without headers.
- When enabled, a configurable bootstrap token can mint short-lived HMAC
  bearer tokens via ``POST /api/auth/token``.
- No external auth dependency: signing uses stdlib ``hmac``/``hashlib`` so the
  project stays lightweight. ``AUTH_JWT_SECRET`` must be strong in production.

Do not put secrets into logs: this module never reads back a token payload
beyond expiry/``sub`` claims.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.app.config import settings
from backend.app.observability.logger import emit

_TOKEN_VERSION = "v1"


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).digest()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(subject: str = "api", ttl_minutes: int | None = None) -> str:
    """Mint a short-lived HMAC-signed bearer token (stateless, no DB)."""
    ttl = ttl_minutes or settings.access_token_ttl_minutes
    now = int(time.time())
    payload = json.dumps(
        {"v": _TOKEN_VERSION, "sub": subject, "iat": now, "exp": now + ttl * 60},
        separators=(",", ":"),
    ).encode("utf-8")
    payload_b64 = _b64encode(payload)
    sig = _b64encode(_sign(payload_b64, settings.auth_jwt_secret))
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> Optional[str]:
    """Return the subject if the token is valid and unexpired, else None."""
    try:
        payload_b64, sig = token.split(".", 1)
        expected = _b64encode(_sign(payload_b64, settings.auth_jwt_secret))
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64decode(payload_b64))
        if payload.get("v") != _TOKEN_VERSION:
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return str(payload.get("sub", "")) or None
    except Exception:  # noqa: BLE001 - malformed tokens are simply rejected
        return None


def require_auth(request: Request) -> None:
    """FastAPI dependency: enforces bearer auth only when ``auth_enabled``.

    When auth is disabled (the default) this is a no-op so local development
    and tests run unauthenticated. ``/health`` and ``/ready`` are never guarded.
    """
    if not settings.auth_enabled:
        return
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        emit("auth_failed", level=30, path=request.url.path, reason="missing_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header.split(" ", 1)[1].strip()
    if verify_token(token) is None:
        emit("auth_failed", level=30, path=request.url.path, reason="invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory fixed-window rate limiter (off unless ``rate_limit_enabled``).

    Tracks successful+failed requests per client IP over a 60s window. Kept in
    process memory only — appropriate for a single-instance lightweight deploy;
    a shared store should replace this for multi-instance scale-out.
    """

    def __init__(self, app, *, per_minute: int | None = None):
        super().__init__(app)
        self.limit = per_minute or settings.rate_limit_per_minute
        self._hits: dict[str, list[float]] = defaultdict(list)

    def _allow(self, client_ip: str) -> bool:
        now = time.monotonic()
        window = [t for t in self._hits.get(client_ip, []) if now - t < 60.0]
        self._hits[client_ip] = window
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if not settings.rate_limit_enabled:
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        if not self._allow(ip):
            emit("rate_limited", level=30, path=request.url.path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Try again shortly."},
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
"""Token issuance endpoint (only functional when authentication is enabled).

Kept deliberately small: when ``AUTH_ENABLED=false`` this endpoint is disabled
so nothing is exposed on a development/benchmark deployment.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.app.api.security import issue_token
from backend.app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenRequest(BaseModel):
    bootstrap_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
def create_token(payload: TokenRequest):
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    if not settings.auth_bootstrap_token or payload.bootstrap_token != settings.auth_bootstrap_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bootstrap token.",
        )
    return TokenResponse(
        access_token=issue_token(subject="api"),
        expires_in_minutes=settings.access_token_ttl_minutes,
    )
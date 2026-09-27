"""Health/metadata endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database.engine import get_session
from backend.app.observability.logger import emit
from backend.app.schemas.api import HealthOut, ReadyOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_session)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "unavailable"
    return HealthOut(
        status="ok" if db_status == "connected" else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database=db_status,
        vector_store=settings.vector_store_mode,
        llm_provider=settings.llm_provider,
    )


def _vector_store_check() -> str:
    try:
        from backend.app.rag.vector_store import VectorStore

        store = VectorStore.from_settings()
        store.close()
        return "ok"
    except Exception:
        return "unavailable"


@router.get("/ready", response_model=ReadyOut)
def ready(db: Session = Depends(get_session)):
    """Readiness probe: verify the dependencies needed to serve requests.

    The LLM provider is intentionally not required: with ``llm_provider=none``
    the system runs its deterministic fallback, so the service is still ready.
    """
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    checks["vector_store"] = _vector_store_check()

    try:
        db.execute(text("SELECT 1 FROM memory_entries LIMIT 1"))
        checks["memory_store"] = "ok"
    except Exception:
        checks["memory_store"] = "unavailable"

    checks["llm_provider"] = "ok"
    is_ready = checks["database"] == "ok" and checks["vector_store"] == "ok"
    emit("readiness_check", status="ready" if is_ready else "not_ready", level=20,
         fields=checks)
    return ReadyOut(
        status="ready" if is_ready else "not_ready",
        version=settings.app_version,
        environment=settings.environment,
        checks=checks,
        llm_provider=settings.llm_provider,
    )

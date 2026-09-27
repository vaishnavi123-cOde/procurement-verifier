from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import auth, cases, documents, executions, health, memory
from backend.app.api.security import RateLimitMiddleware
from backend.app.config import PROJECT_ROOT, settings
from backend.app.database.engine import init_db
from backend.app.observability.logger import configure_logging, emit, error_category, new_request_id
from backend.app.observability.middleware import ObservabilityMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the database and structured logging on startup
    configure_logging()
    init_db()
    yield
    # Cleanup on shutdown


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Evidence-Backed Procurement Decision & Verification System API",
    lifespan=lifespan,
)

# CORS configuration: origins are configurable via CORS_ORIGINS (JSON array in
# the environment). The credentialless default keeps localhost dev simple while
# a locked-down list is used in production.
_allow_all = settings.cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials and not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Rate limiting (off by default)
if settings.rate_limit_enabled:
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
# Observability: request ids + timing
app.add_middleware(ObservabilityMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Safe error responses: never leak internals unless explicitly enabled.

    The full exception (including traceback) is written to the structured
    server log so operators keep visibility without exposing internals to
    callers. HTTPException / validation errors keep FastAPI's default handling.
    """
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    emit(
        "unhandled_exception",
        message="Unhandled exception",
        level=40,
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        error_category=error_category(exc),
        error=str(exc),
    )
    if settings.show_error_detail:
        return JSONResponse(status_code=500, content={"detail": str(exc)})
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# Include routers. Health/readiness stay unauthenticated; API routers are
# guarded only when AUTH_ENABLED=true.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(documents.router)
app.include_router(memory.cases_router)
app.include_router(memory.suppliers_router)
app.include_router(executions.router)


# --- Static frontend (local production-style serving) -----------------------
# Serve the compiled Vite build from the FastAPI app so no separate web server
# (nginx/Docker) is required. API/docs routes are registered above and therefore
# take precedence over this catch-all. When no build exists the API still works.
_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
_SPA_INDEX = _FRONTEND_DIST / "index.html"

if (_SPA_INDEX.is_file() and (_FRONTEND_DIST / "assets").is_dir()):
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_spa(full_path: str):
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(_FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        return FileResponse(_SPA_INDEX)
else:
    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_unavailable(full_path: str):
        return JSONResponse(
            status_code=404,
            content={"detail": "Frontend build not found. Run `npm run build` in frontend/."},
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000)
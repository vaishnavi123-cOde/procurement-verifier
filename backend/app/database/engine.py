"""SQLAlchemy engine / session management.

Supports both SQLite (local development and tests) and PostgreSQL (Docker).
The vector store in-memory mode and SQLite are the zero-dependency fallbacks.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_connect_timeout,
        "connect_args": {"connect_timeout": settings.db_connect_timeout},
    }


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a Session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaction-scoped session for use outside FastAPI (scripts, agents)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables and apply tracked migrations.

    Serves as startup validation: connections are verified lazily by the
    ``SELECT 1`` in :func:`verify_connection` after the schema is created.
    """
    from backend.app.models import ensure_models_imported

    ensure_models_imported()
    Base.metadata.create_all(engine)
    _run_migrations()
    verify_connection()


def verify_connection() -> None:
    """Raise a clear error if the configured database is unreachable.

    Used at startup so misconfigured ``DATABASE_URL`` values (e.g. a PostgreSQL
    URL in a non-Docker environment) fail loudly instead of at first request.
    """
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external server
        raise RuntimeError(
            f"Database is unreachable at startup: {exc}. "
            "Check DATABASE_URL and that the server is running."
        ) from exc


_MIGRATIONS: list[tuple[str, "str | Callable[[Session], None]"]] = [
    # (name, sql | migration_fn). SQL is executed for databases that support
    # multi-statement; callables allow guarded DDL. Each migration must be
    # idempotent-safe (guarded by the version table).
    ("v0_initial", """-- baseline schema is managed by create_all()"""),
    ("v1_retrieval_log_metadata", lambda session: _add_column_if_missing(
        session, "retrieval_logs", "metadata_json", "JSON")),
    ("v2_required_indexes", lambda session: _create_indexes(session)),
]


_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_case_documents_sha256", "case_documents", "sha256"),
    ("ix_agent_executions_request_id", "agent_executions", "request_id"),
    ("ix_agent_executions_case_started", "agent_executions", "case_id, started_at"),
    ("ix_tool_calls_request_id", "tool_calls", "request_id"),
    ("ix_llm_calls_request_id", "llm_calls", "request_id"),
    ("ix_retrieval_logs_request_id", "retrieval_logs", "request_id"),
    ("ix_execution_spans_request_id", "execution_spans", "request_id"),
    ("ix_memory_entries_scope_key", "memory_entries", "scope, scope_key"),
    ("ix_memory_entries_source_case", "memory_entries", "source_case_id"),
    ("ix_evidence_case_supplier", "evidence", "case_id, supplier_id"),
)


def _create_indexes(session: Session) -> None:
    """Idempotently add indexes required for read paths (SQLite + PostgreSQL)."""
    bind = session.get_bind()
    try:
        existing = {c["name"] for c in inspect(bind).get_table_names()}
    except Exception:
        return
    for name, table, columns in _INDEXES:
        if table not in existing:
            continue
        try:
            session.execute(text(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
            ))
            session.commit()
        except Exception:  # pragma: no cover - DB-specific guard
            session.rollback()
            continue


def _add_column_if_missing(session: Session, table: str, column: str, ddl_type: str) -> None:
    """Idempotently add a column to an existing table (SQLite + PostgreSQL)."""
    try:
        columns = {c["name"] for c in inspect(session.get_bind()).get_columns(table)}
    except Exception:
        return
    if column in columns:
        return
    session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def _run_migrations() -> None:
    with SessionLocal() as session:
        session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        session.commit()
        applied = {
            row[0]
            for row in session.execute(text("SELECT name FROM schema_migrations")).fetchall()
        }
        for name, migration in _MIGRATIONS:
            if name in applied:
                continue
            if callable(migration):
                migration(session)
            elif migration.strip():
                for statement in migration.split(";"):
                    if statement.strip():
                        session.execute(text(statement))
            session.execute(text("INSERT INTO schema_migrations(name) VALUES (:n)"), {"n": name})
            session.commit()


def dispose_engine() -> None:
    engine.dispose()
"""Shared pytest fixtures for the LangGraph pipeline tests.

Sets a fresh, isolated SQLite database + in-memory vector store *before* any
backend module is imported (the engine is created at import time).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="pv-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(_TMP) / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
os.environ["VECTOR_STORE_MODE"] = "memory"
os.environ["LLM_PROVIDER"] = "none"


@pytest.fixture(scope="session")
def db_session():
    from backend.app.database.engine import SessionLocal, init_db
    from backend.app.agents.llm import reset_llm

    init_db()
    reset_llm()
    with SessionLocal() as session:
        yield session


@pytest.fixture(scope="function")
def graph_state():
    from backend.app.graphs.state import initial_state

    return initial_state(case_id="bench-graph-test", request_id="req-test-1")


@pytest.fixture(scope="session")
def bench_case_dir():
    from backend.app.config import settings

    directory = Path(settings.dataset_root) / "synthetic" / "cases" / "bench-006"
    assert directory.is_dir(), f"dataset case directory missing: {directory}"
    return directory
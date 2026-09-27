"""Shared ORM helpers: timestamp mixin and id generation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware UTC now (SQLite stores naive; we normalize on write)."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


# Ensure ORM model modules are imported before metadata.create_all
def ensure_models_imported() -> None:
    import backend.app.models.evidence  # noqa: F401
    import backend.app.models.execution  # noqa: F401
    import backend.app.models.memory  # noqa: F401
    import backend.app.models.procurement  # noqa: F401
    import backend.app.models.evaluation  # noqa: F401
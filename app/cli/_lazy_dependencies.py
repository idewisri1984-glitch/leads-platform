from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def SessionLocal() -> Session:
    """Resolve the configured session factory only when a command needs it."""
    from app.core.database.session import SessionLocal as session_factory

    return session_factory()

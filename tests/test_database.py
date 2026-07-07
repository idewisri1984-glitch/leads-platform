from sqlalchemy import text

from app.core.database.engine import engine


def test_database_connection() -> None:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))

        assert result.scalar() == 1


def test_sqlite_foreign_keys_are_enabled() -> None:
    with engine.connect() as connection:
        result = connection.execute(text("PRAGMA foreign_keys"))

        assert result.scalar() == 1

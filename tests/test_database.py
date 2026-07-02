from sqlalchemy import text

from app.core.database.engine import engine


def test_database_connection() -> None:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))

        assert result.scalar() == 1
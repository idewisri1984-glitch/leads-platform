from sqlalchemy import Engine, create_engine

from app.core.config.settings import settings


def create_database_engine() -> Engine:
    """
    Create SQLAlchemy engine.
    """

    return create_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
    )


engine: Engine = create_database_engine()

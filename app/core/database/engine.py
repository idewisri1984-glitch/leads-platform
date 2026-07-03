from sqlalchemy import create_engine

from app.core.config.settings import settings

engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
)

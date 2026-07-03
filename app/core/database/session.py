from sqlalchemy.orm import Session, sessionmaker

from app.core.database.engine import engine

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)

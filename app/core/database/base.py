from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Base class for all ORM models.
    """

    pass


# Import all ORM models after Base is defined.
# This ensures SQLAlchemy registers relationships.
import app.modules  # noqa: E402,F401

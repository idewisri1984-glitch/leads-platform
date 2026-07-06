from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base


class Project(Base):
    """
    Project entity.

    Root object of the platform.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    companies = relationship(
        "Company",
        back_populates="project",
        cascade="all, delete-orphan",
    )

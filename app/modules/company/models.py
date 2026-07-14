from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base


class Company(Base):
    """
    Company entity.

    Represents an organization belonging to a project.
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    website: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    country: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    city: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    industry: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="NEW",
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    project = relationship(
        "Project",
        back_populates="companies",
    )

    contacts = relationship(
        "Contact",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    leads = relationship(
        "Lead",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    enrichment = relationship(
        "CompanyEnrichment",
        back_populates="company",
        cascade="all, delete-orphan",
        uselist=False,
    )

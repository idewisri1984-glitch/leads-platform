from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base


class Contact(Base):
    """
    Contact entity.

    Represents a person or generic business contact associated with a company.
    """

    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint(
            "NULLIF(TRIM(first_name), '') IS NOT NULL "
            "OR NULLIF(TRIM(last_name), '') IS NOT NULL "
            "OR NULLIF(TRIM(email), '') IS NOT NULL "
            "OR NULLIF(TRIM(phone), '') IS NOT NULL "
            "OR NULLIF(TRIM(linkedin_url), '') IS NOT NULL "
            "OR NULLIF(TRIM(instagram_url), '') IS NOT NULL",
            name="ck_contacts_meaningful_identity",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    first_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    last_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    job_title: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    linkedin_url: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    instagram_url: Mapped[str | None] = mapped_column(
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

    source: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(255),
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

    company = relationship(
        "Company",
        back_populates="contacts",
    )

    leads = relationship(
        "Lead",
        back_populates="contact",
    )

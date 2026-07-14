from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class EnrichmentStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


class CompanyEnrichment(Base):
    """Latest enrichment data for one saved company."""

    __tablename__ = "company_enrichments"
    __table_args__ = (
        CheckConstraint(
            "enrichment_status IN ('PENDING', 'SUCCEEDED', 'PARTIAL', 'NOT_FOUND', 'FAILED')",
            name="ck_company_enrichments_enrichment_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    enrichment_status: Mapped[EnrichmentStatus] = mapped_column(
        String(50), default=EnrichmentStatus.PENDING, nullable=False
    )
    website_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(100))
    instagram_url: Mapped[str | None] = mapped_column(String(500))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    contact_page_url: Mapped[str | None] = mapped_column(String(500))
    about_page_url: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    company = relationship("Company", back_populates="enrichment")

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContactDiscoveryStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


class ContactDiscoveryCandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    REVIEWED = "REVIEWED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class ContactDiscoverySourceType(StrEnum):
    CONTACT_PAGE = "CONTACT_PAGE"
    ABOUT_PAGE = "ABOUT_PAGE"
    TEAM_PAGE = "TEAM_PAGE"
    LEADERSHIP_PAGE = "LEADERSHIP_PAGE"
    STAFF_PAGE = "STAFF_PAGE"
    OTHER_PUBLIC_PAGE = "OTHER_PUBLIC_PAGE"


class CompanyContactDiscoveryState(Base):
    __tablename__ = "company_contact_discovery_states"
    __table_args__ = (
        CheckConstraint(
            "discovery_status IN ('PENDING', 'SUCCEEDED', 'PARTIAL', 'NOT_FOUND', 'FAILED')",
            name="ck_company_contact_discovery_states_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    provider: Mapped[str | None] = mapped_column(String(100))
    discovery_status: Mapped[ContactDiscoveryStatus] = mapped_column(
        String(50), default=ContactDiscoveryStatus.PENDING, nullable=False
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ContactDiscoveryCandidate(Base):
    __tablename__ = "contact_discovery_candidates"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "deduplication_key",
            name="uq_contact_discovery_candidates_company_deduplication_key",
        ),
        CheckConstraint(
            "source_type IN ('CONTACT_PAGE', 'ABOUT_PAGE', 'TEAM_PAGE', "
            "'LEADERSHIP_PAGE', 'STAFF_PAGE', 'OTHER_PUBLIC_PAGE')",
            name="ck_contact_discovery_candidates_source_type",
        ),
        CheckConstraint(
            "discovery_status IN ('DISCOVERED', 'REVIEWED', 'PROMOTED', 'REJECTED')",
            name="ck_contact_discovery_candidates_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_contact_discovery_candidates_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    normalized_email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(100))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    instagram_url: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[ContactDiscoverySourceType] = mapped_column(String(50), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discovery_status: Mapped[ContactDiscoveryCandidateStatus] = mapped_column(
        String(50), default=ContactDiscoveryCandidateStatus.DISCOVERED, nullable=False
    )
    deduplication_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

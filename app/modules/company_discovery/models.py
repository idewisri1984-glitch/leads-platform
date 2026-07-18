from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CompanyDiscoveryRunStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


class CompanyDiscoveryCandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    REVIEWED = "REVIEWED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class CompanyDiscoveryRun(Base):
    __tablename__ = "company_discovery_runs"
    __table_args__ = (
        CheckConstraint(
            "run_status IN ('PENDING', 'SUCCEEDED', 'PARTIAL', 'NOT_FOUND', 'FAILED')",
            name="ck_company_discovery_runs_status",
        ),
        CheckConstraint("query_count >= 0", name="ck_company_discovery_runs_query_count"),
        CheckConstraint("result_count >= 0", name="ck_company_discovery_runs_result_count"),
        CheckConstraint("candidate_count >= 0", name="ck_company_discovery_runs_candidate_count"),
        Index("ix_company_discovery_runs_project_status", "project_id", "run_status"),
        Index(
            "ix_company_discovery_runs_project_fingerprint",
            "project_id",
            "request_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    search_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_profiles.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    run_status: Mapped[CompanyDiscoveryRunStatus] = mapped_column(
        String(50),
        default=CompanyDiscoveryRunStatus.PENDING,
        server_default=CompanyDiscoveryRunStatus.PENDING,
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    query_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CompanyDiscoveryCandidate(Base):
    __tablename__ = "company_discovery_candidates"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "identity_key",
            name="uq_company_discovery_candidates_project_identity",
        ),
        CheckConstraint(
            "candidate_status IN ('DISCOVERED', 'REVIEWED', 'PROMOTED', 'REJECTED')",
            name="ck_company_discovery_candidates_status",
        ),
        CheckConstraint(
            "best_position IS NULL OR best_position >= 1",
            name="ck_company_discovery_candidates_best_position",
        ),
        CheckConstraint(
            "country_code IS NULL OR (length(country_code) = 2 AND country_code GLOB '[A-Z][A-Z]')",
            name="ck_company_discovery_candidates_country_code",
        ),
        Index(
            "ix_company_discovery_candidates_project_status",
            "project_id",
            "candidate_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("company_discovery_runs.id"), nullable=False, index=True
    )
    last_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("company_discovery_runs.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    normalized_name: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(500))
    website_identity: Mapped[str | None] = mapped_column(String(300))
    country_code: Mapped[str | None] = mapped_column(String(2))
    identity_key: Mapped[str] = mapped_column(String(700), nullable=False)
    best_position: Mapped[int | None] = mapped_column(Integer)
    candidate_status: Mapped[CompanyDiscoveryCandidateStatus] = mapped_column(
        String(50),
        default=CompanyDiscoveryCandidateStatus.DISCOVERED,
        server_default=CompanyDiscoveryCandidateStatus.DISCOVERED,
        nullable=False,
    )
    promoted_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

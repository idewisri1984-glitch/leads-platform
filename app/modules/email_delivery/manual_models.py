from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class ManualEmailSendRecord(Base):
    __tablename__ = "manual_email_send_records"
    __table_args__ = (
        UniqueConstraint(
            "email_draft_id",
            name="uq_manual_email_send_records_email_draft_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    email_draft_id: Mapped[int] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

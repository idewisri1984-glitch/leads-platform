from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class EmailDraftStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EmailDraft(Base):
    __tablename__ = "email_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'REJECTED')",
            name="ck_email_drafts_status",
        ),
        UniqueConstraint("request_fingerprint", name="uq_email_drafts_request_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(201), nullable=False)
    recipient_role: Mapped[str | None] = mapped_column(String(150))
    sender_name: Mapped[str] = mapped_column(String(150), nullable=False)
    sender_company: Mapped[str] = mapped_column(String(200), nullable=False)
    generation_tone: Mapped[str] = mapped_column(String(30), nullable=False)
    generation_purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    generation_value_proposition: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=EmailDraftStatus.DRAFT.value, nullable=False, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


_IMMUTABLE_REVIEWED_FIELDS = (
    "project_id",
    "company_id",
    "contact_id",
    "lead_id",
    "task_id",
    "recipient_email",
    "recipient_name",
    "recipient_role",
    "sender_name",
    "sender_company",
    "generation_tone",
    "generation_purpose",
    "generation_value_proposition",
    "subject",
    "text_body",
    "language",
    "prompt_version",
    "provider",
    "model",
    "context_fingerprint",
    "request_fingerprint",
    "content_hash",
    "status",
)


@event.listens_for(EmailDraft, "before_update")
def prevent_reviewed_draft_mutation(
    _mapper: object, _connection: object, target: EmailDraft
) -> None:
    state = inspect(target)
    status_history = state.attrs.status.history
    original = status_history.deleted[0] if status_history.deleted else target.status
    if original not in {EmailDraftStatus.APPROVED.value, EmailDraftStatus.REJECTED.value}:
        return
    if any(state.attrs[field].history.has_changes() for field in _IMMUTABLE_REVIEWED_FIELDS):
        raise ValueError("Reviewed email draft content is immutable.")

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class EmailDeliveryOutcome(StrEnum):
    RESERVED = "RESERVED"
    ACCEPTED = "ACCEPTED"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    UNKNOWN = "UNKNOWN"


_TERMINAL_OUTCOMES = frozenset(
    {
        EmailDeliveryOutcome.ACCEPTED.value,
        EmailDeliveryOutcome.TRANSIENT_FAILURE.value,
        EmailDeliveryOutcome.PERMANENT_FAILURE.value,
        EmailDeliveryOutcome.UNKNOWN.value,
    }
)


class EmailDeliveryAttempt(Base):
    __tablename__ = "email_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "email_draft_id",
            name="uq_email_delivery_attempts_email_draft_id",
        ),
        UniqueConstraint(
            "attempt_key",
            name="uq_email_delivery_attempts_attempt_key",
        ),
        UniqueConstraint(
            "message_id",
            name="uq_email_delivery_attempts_message_id",
        ),
        CheckConstraint(
            "outcome IN "
            "('RESERVED', 'ACCEPTED', 'TRANSIENT_FAILURE', "
            "'PERMANENT_FAILURE', 'UNKNOWN')",
            name="ck_email_delivery_attempts_outcome",
        ),
        CheckConstraint(
            "smtp_classification IS NULL OR "
            "smtp_classification IN ('TRANSIENT', 'PERMANENT', 'UNKNOWN')",
            name="ck_email_delivery_attempts_classification",
        ),
        CheckConstraint(
            "smtp_code IS NULL OR (smtp_code >= 200 AND smtp_code <= 599)",
            name="ck_email_delivery_attempts_smtp_code",
        ),
        CheckConstraint(
            "length(trim(attempt_key)) = 64 AND lower(attempt_key) = attempt_key AND "
            "length(trim(recipient_email)) > 0 AND "
            "length(trim(envelope_from)) > 0 AND "
            "length(trim(header_from_email)) > 0 AND "
            "(header_from_name IS NULL OR length(trim(header_from_name)) > 0) AND "
            "(reply_to IS NULL OR length(trim(reply_to)) > 0) AND "
            "length(trim(message_id)) > 0 AND "
            "length(trim(content_hash)) = 64 AND lower(content_hash) = content_hash AND "
            "length(trim(transport_name)) > 0 AND "
            "security_mode IN ('STARTTLS', 'TLS_IMPLICIT', "
            "'PLAINTEXT_LOCAL_TEST_ONLY')",
            name="ck_email_delivery_attempts_nonblank_identity",
        ),
        CheckConstraint(
            "(outcome = 'RESERVED' AND completed_at IS NULL AND accepted_at IS NULL "
            "AND unknown_at IS NULL AND smtp_classification IS NULL "
            "AND smtp_code IS NULL AND error_category IS NULL) OR "
            "(outcome = 'ACCEPTED' AND completed_at IS NOT NULL "
            "AND accepted_at IS NOT NULL AND unknown_at IS NULL "
            "AND smtp_classification IS NULL AND error_category IS NULL) OR "
            "(outcome = 'TRANSIENT_FAILURE' AND completed_at IS NOT NULL "
            "AND accepted_at IS NULL AND unknown_at IS NULL "
            "AND smtp_classification = 'TRANSIENT') OR "
            "(outcome = 'PERMANENT_FAILURE' AND completed_at IS NOT NULL "
            "AND accepted_at IS NULL AND unknown_at IS NULL "
            "AND smtp_classification = 'PERMANENT') OR "
            "(outcome = 'UNKNOWN' AND completed_at IS NOT NULL "
            "AND accepted_at IS NULL AND unknown_at IS NOT NULL "
            "AND smtp_classification = 'UNKNOWN')",
            name="ck_email_delivery_attempts_outcome_fields",
        ),
        Index("ix_email_delivery_attempts_outcome", "outcome"),
        Index("ix_email_delivery_attempts_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email_draft_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_drafts.id",
            name="fk_email_delivery_attempts_email_draft_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    attempt_key: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(30), default=EmailDeliveryOutcome.RESERVED.value, nullable=False
    )
    recipient_email: Mapped[str] = mapped_column(String(254), nullable=False)
    envelope_from: Mapped[str] = mapped_column(String(254), nullable=False)
    header_from_email: Mapped[str] = mapped_column(String(254), nullable=False)
    header_from_name: Mapped[str | None] = mapped_column(String(100))
    reply_to: Mapped[str | None] = mapped_column(String(254))
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    transport_name: Mapped[str] = mapped_column(String(100), nullable=False)
    security_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    smtp_classification: Mapped[str | None] = mapped_column(String(20))
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unknown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


_IMMUTABLE_IDENTITY_FIELDS = (
    "email_draft_id",
    "attempt_key",
    "recipient_email",
    "envelope_from",
    "header_from_email",
    "header_from_name",
    "reply_to",
    "message_id",
    "content_hash",
    "transport_name",
    "security_mode",
    "created_at",
)
_OUTCOME_FIELDS = (
    "outcome",
    "smtp_classification",
    "smtp_code",
    "error_category",
    "completed_at",
    "accepted_at",
    "unknown_at",
    "updated_at",
)


@event.listens_for(EmailDeliveryAttempt, "before_update")
def prevent_invalid_delivery_attempt_mutation(
    _mapper: object, _connection: object, target: EmailDeliveryAttempt
) -> None:
    state = inspect(target)
    if any(state.attrs[field].history.has_changes() for field in _IMMUTABLE_IDENTITY_FIELDS):
        raise ValueError("Email delivery attempt identity is immutable.")

    outcome_history = state.attrs.outcome.history
    original = outcome_history.deleted[0] if outcome_history.deleted else target.outcome
    outcome_changed = outcome_history.has_changes()
    if not outcome_changed:
        if any(state.attrs[field].history.has_changes() for field in _OUTCOME_FIELDS[1:]):
            raise ValueError("Email delivery attempt outcome requires a controlled transition.")
        return
    if original != EmailDeliveryOutcome.RESERVED.value or target.outcome not in _TERMINAL_OUTCOMES:
        raise ValueError("Email delivery attempt transition is invalid.")

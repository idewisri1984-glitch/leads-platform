from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EmailDeliveryAttempt, EmailDeliveryOutcome
from .schemas import EmailDeliveryAttemptCreate, EmailDeliveryAttemptOutcomeUpdate


class EmailDeliveryAttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def reserve(self, data: EmailDeliveryAttemptCreate) -> EmailDeliveryAttempt:
        if type(data) is not EmailDeliveryAttemptCreate:
            raise ValueError("Email delivery reservation is invalid.")
        try:
            validated = EmailDeliveryAttemptCreate(**data.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise ValueError("Email delivery reservation is invalid.") from None
        attempt = EmailDeliveryAttempt(
            email_draft_id=validated.email_draft_id,
            attempt_key=validated.attempt_key,
            outcome=validated.outcome.value,
            recipient_email=validated.recipient_email,
            envelope_from=validated.envelope_from,
            header_from_email=validated.header_from_email,
            header_from_name=validated.header_from_name,
            reply_to=validated.reply_to,
            message_id=validated.message_id,
            content_hash=validated.content_hash,
            transport_name=validated.transport_name,
            security_mode=validated.security_mode,
            smtp_classification=None,
            smtp_code=None,
            error_category=None,
            created_at=validated.created_at,
            completed_at=None,
            accepted_at=None,
            unknown_at=None,
            updated_at=validated.created_at,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def get(self, attempt_id: int) -> EmailDeliveryAttempt | None:
        return self.session.get(EmailDeliveryAttempt, attempt_id)

    def get_by_email_draft_id(self, email_draft_id: int) -> EmailDeliveryAttempt | None:
        return self.session.scalar(
            select(EmailDeliveryAttempt).where(
                EmailDeliveryAttempt.email_draft_id == email_draft_id
            )
        )

    def get_by_attempt_key(self, attempt_key: str) -> EmailDeliveryAttempt | None:
        return self.session.scalar(
            select(EmailDeliveryAttempt).where(EmailDeliveryAttempt.attempt_key == attempt_key)
        )

    def transition(
        self, attempt_id: int, data: EmailDeliveryAttemptOutcomeUpdate
    ) -> EmailDeliveryAttempt:
        if type(attempt_id) is not int or attempt_id <= 0:
            raise ValueError("Email delivery attempt identifier is invalid.")
        if type(data) is not EmailDeliveryAttemptOutcomeUpdate:
            raise ValueError("Email delivery outcome transition is invalid.")
        try:
            validated = EmailDeliveryAttemptOutcomeUpdate(**data.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise ValueError("Email delivery outcome transition is invalid.") from None
        attempt = self.session.scalar(
            select(EmailDeliveryAttempt)
            .where(EmailDeliveryAttempt.id == attempt_id)
            .with_for_update()
        )
        if attempt is None:
            raise ValueError("Email delivery attempt was not found.")
        if attempt.outcome != EmailDeliveryOutcome.RESERVED.value:
            raise ValueError("Email delivery attempt transition is invalid.")
        attempt.outcome = validated.outcome.value
        attempt.smtp_classification = (
            None if validated.smtp_classification is None else validated.smtp_classification.value
        )
        attempt.smtp_code = validated.smtp_code
        attempt.error_category = validated.error_category
        attempt.completed_at = validated.completed_at
        attempt.accepted_at = validated.accepted_at
        attempt.unknown_at = validated.unknown_at
        attempt.updated_at = validated.completed_at
        self.session.flush()
        return attempt

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_serializer, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact_discovery.normalization import normalize_discovered_email
from app.modules.email_draft.context import build_content_hash
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task, TaskLifecycleStatus
from app.providers.smtp.contracts import (
    SMTPDeliveryReceipt,
    SMTPMessageEnvelope,
    SMTPSecurityMode,
    SMTPSenderIdentity,
)
from app.providers.smtp.errors import (
    SMTPAuthenticationFailedError,
    SMTPConfigurationError,
    SMTPConnectionFailedError,
    SMTPDataRejectedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPFailureClassification,
    SMTPInternalError,
    SMTPProtocolError,
    SMTPRecipientRejectedError,
    SMTPSenderRejectedError,
    SMTPTimeoutError,
    SMTPTLSNegotiationError,
    SMTPTLSUnavailableError,
    SMTPTransportError,
)
from app.providers.smtp.interfaces import SMTPTransport

from .manual_repository import ManualEmailSendRecordRepository
from .models import EmailDeliveryAttempt, EmailDeliveryOutcome
from .outreach_mode import EmailDeliveryMode, claim_email_delivery_mode
from .repository import EmailDeliveryAttemptRepository
from .schemas import (
    EmailDeliveryAttemptCreate,
    EmailDeliveryAttemptOutcomeUpdate,
    EmailDeliverySMTPClassification,
)

_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)
_ATTEMPT_NAMESPACE = "leads-platform:email-delivery-attempt:v1"
_CONFIRMATION = "Email delivery requires explicit confirmation."
_INVALID = "Email delivery command is invalid."
_NOT_FOUND = "Email delivery target was not found in the requested scope."
_NOT_APPROVED = "Email draft is not approved for delivery."
_STALE = "Email delivery reviewed context is stale."
_ALREADY_ATTEMPTED = "Email delivery was already attempted."
_CONFIGURATION = "Email delivery configuration is invalid."
_TRANSIENT = "Email delivery failed transiently; retry is not supported."
_PERMANENT = "Email delivery failed permanently."
_UNKNOWN = "Email delivery outcome is unknown; retry is not supported."
_RECOVERY = "Email delivery persistence requires operator recovery."
_TRANSACTION = "Email delivery requires an unowned session transaction boundary."
_INTERNAL = "Email delivery failed."


def _positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Email delivery identifier is invalid.")
    return value


def _safe_header(value: object, maximum: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or value != value.strip() or not 1 <= len(value) <= maximum:
        raise ValueError("Email delivery configuration is invalid.")
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("Email delivery configuration is invalid.")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Email delivery configuration is invalid.")
    return value


def _trusted_domain(value: object) -> str:
    domain = _safe_header(value, 253)
    assert domain is not None
    normalized = domain.casefold()
    labels = normalized.split(".")
    if (
        not normalized.isascii()
        or len(labels) < 2
        or any(
            not 1 <= len(label) <= 63
            or not label[0].isalnum()
            or not label[-1].isalnum()
            or any(not character.isalnum() and character != "-" for character in label)
            for label in labels
        )
    ):
        raise ValueError("Email delivery Message-ID domain is invalid.")
    return normalized


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise EmailDeliveryConfigurationError(_CONFIGURATION)
    return value.astimezone(UTC)


def _database_utc(value: datetime | None) -> datetime:
    if type(value) is not datetime:
        raise EmailDeliveryPersistenceRecoveryRequiredError(_RECOVERY)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_smtp_code(value: object) -> int | None:
    if type(value) is int and 200 <= value <= 599:
        return value
    return None


class TrustedEmailSenderConfig(BaseModel):
    model_config = _STRICT

    envelope_from: str
    header_from_email: str
    header_from_name: str | None
    reply_to: str | None
    message_id_domain: str
    transport_name: str
    security_mode: SMTPSecurityMode

    @field_validator("envelope_from", mode="before")
    @classmethod
    def validate_envelope_from(cls, value: object) -> str:
        try:
            return SMTPSenderIdentity(email=value).email
        except (ValidationError, TypeError, ValueError):
            raise ValueError("Email delivery envelope sender is invalid.") from None

    @field_validator("header_from_email", mode="before")
    @classmethod
    def validate_header_from_email(cls, value: object) -> str:
        try:
            return SMTPSenderIdentity(email=value).email
        except (ValidationError, TypeError, ValueError):
            raise ValueError("Email delivery header sender is invalid.") from None

    @field_validator("header_from_name", mode="before")
    @classmethod
    def validate_header_from_name(cls, value: object) -> str | None:
        return _safe_header(value, 100, optional=True)

    @field_validator("reply_to", mode="before")
    @classmethod
    def validate_reply_to(cls, value: object) -> str | None:
        if value is None:
            return None
        try:
            return SMTPSenderIdentity(email="sender@example.test", reply_to=value).reply_to
        except (ValidationError, TypeError, ValueError):
            raise ValueError("Email delivery Reply-To is invalid.") from None

    @field_validator("message_id_domain", mode="before")
    @classmethod
    def validate_message_id_domain(cls, value: object) -> str:
        return _trusted_domain(value)

    @field_validator("transport_name", mode="before")
    @classmethod
    def validate_transport_name(cls, value: object) -> str:
        transport = _safe_header(value, 100)
        assert transport is not None
        return transport

    @field_validator("security_mode", mode="before")
    @classmethod
    def validate_security_mode(cls, value: object) -> SMTPSecurityMode:
        if type(value) is not SMTPSecurityMode:
            raise ValueError("Email delivery security mode is invalid.")
        return value


class ConfirmedEmailSendCommand(BaseModel):
    model_config = _STRICT

    project_id: int
    company_id: int
    contact_id: int
    email_draft_id: int
    confirmed: Literal[True]

    @field_validator("project_id", "company_id", "contact_id", "email_draft_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> int:
        return _positive_id(value)

    @field_validator("confirmed", mode="before")
    @classmethod
    def validate_confirmation(cls, value: object) -> Literal[True]:
        if type(value) is not bool or value is not True:
            raise ValueError("Email delivery confirmation is required.")
        return True


class ConfirmedEmailSendResult(BaseModel):
    model_config = _STRICT

    email_draft_id: int
    delivery_attempt_id: int
    recipient: str
    message_id: str
    outcome: EmailDeliveryOutcome
    created_at: datetime
    completed_at: datetime
    accepted_at: datetime | None
    smtp_classification: EmailDeliverySMTPClassification | None
    smtp_code: int | None
    error_category: str | None

    @field_serializer("created_at", "completed_at", "accepted_at", when_used="json")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return utc_value.isoformat().replace("+00:00", "Z")


class EmailDeliveryServiceError(ValueError):
    pass


class EmailDeliveryConfirmationRequiredError(EmailDeliveryServiceError):
    pass


class EmailDeliveryInvalidCommandError(EmailDeliveryServiceError):
    pass


class EmailDeliveryNotFoundError(EmailDeliveryServiceError):
    pass


class EmailDeliveryNotApprovedError(EmailDeliveryServiceError):
    pass


class EmailDeliveryStaleContextError(EmailDeliveryServiceError):
    pass


class EmailDeliveryAlreadyAttemptedError(EmailDeliveryServiceError):
    def __init__(self, attempt_id: int | None = None, outcome: str | None = None) -> None:
        super().__init__(_ALREADY_ATTEMPTED)
        self.attempt_id = attempt_id
        self.outcome = outcome


class EmailDeliveryConfigurationError(EmailDeliveryServiceError):
    pass


class EmailDeliveryTransientFailureError(EmailDeliveryServiceError):
    pass


class EmailDeliveryPermanentFailureError(EmailDeliveryServiceError):
    pass


class EmailDeliveryUnknownOutcomeError(EmailDeliveryServiceError):
    pass


class EmailDeliveryPersistenceRecoveryRequiredError(EmailDeliveryServiceError):
    pass


class EmailDeliveryTransactionBoundaryError(EmailDeliveryServiceError):
    pass


class EmailDeliveryInternalError(EmailDeliveryServiceError):
    pass


def build_delivery_attempt_key(
    *,
    project_id: int,
    email_draft_id: int,
    content_hash: str,
    recipient_email: str,
    message_id_domain: str,
) -> str:
    payload = json.dumps(
        {
            "content_hash": content_hash,
            "email_draft_id": email_draft_id,
            "message_id_domain": message_id_domain,
            "namespace": _ATTEMPT_NAMESPACE,
            "project_id": project_id,
            "recipient_email": recipient_email,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def build_delivery_message_id(attempt_key: str, message_id_domain: str) -> str:
    return f"<ed-{attempt_key[:48]}@{message_id_domain}>"


class ConfirmedEmailSendService:
    """Own TX1 reservation commit, one SMTP call, and TX2 terminal commit."""

    def __init__(
        self,
        *,
        session: Session,
        repository: EmailDeliveryAttemptRepository,
        transport: SMTPTransport,
        sender: TrustedEmailSenderConfig,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if getattr(repository, "session", None) is not session:
            raise EmailDeliveryTransactionBoundaryError(_TRANSACTION)
        if type(sender) is not TrustedEmailSenderConfig:
            raise EmailDeliveryConfigurationError(_CONFIGURATION)
        try:
            validated_sender = TrustedEmailSenderConfig(**sender.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise EmailDeliveryConfigurationError(_CONFIGURATION) from None
        self.session = session
        self.repository = repository
        self.transport = transport
        self.sender = validated_sender
        self.clock = clock or (lambda: datetime.now(UTC))

    def send(self, command: ConfirmedEmailSendCommand) -> ConfirmedEmailSendResult:
        data = self._validated_command(command)
        if self.session.in_transaction():
            raise EmailDeliveryTransactionBoundaryError(_TRANSACTION)
        try:
            draft = self._load_authoritative_draft(data)
            existing = self.repository.get_by_email_draft_id(draft.id)
            if existing is not None:
                raise EmailDeliveryAlreadyAttemptedError(existing.id, existing.outcome)
            manual_record = ManualEmailSendRecordRepository(self.session).get_by_email_draft_id(
                draft.id
            )
            if manual_record is not None:
                raise EmailDeliveryAlreadyAttemptedError(
                    manual_record.id, EmailDeliveryMode.MANUAL.value
                )
            if not claim_email_delivery_mode(
                self.session,
                email_draft_id=draft.id,
                mode=EmailDeliveryMode.AUTOMATIC,
            ):
                raise EmailDeliveryAlreadyAttemptedError()
            created_at = _utc(self.clock())
            attempt_key = build_delivery_attempt_key(
                project_id=draft.project_id,
                email_draft_id=draft.id,
                content_hash=draft.content_hash,
                recipient_email=draft.recipient_email,
                message_id_domain=self.sender.message_id_domain,
            )
            message_id = build_delivery_message_id(attempt_key, self.sender.message_id_domain)
            envelope = self._envelope(draft, message_id, created_at)
            attempt = self.repository.reserve(
                EmailDeliveryAttemptCreate(
                    email_draft_id=draft.id,
                    attempt_key=attempt_key,
                    outcome=EmailDeliveryOutcome.RESERVED,
                    recipient_email=draft.recipient_email,
                    envelope_from=self.sender.envelope_from,
                    header_from_email=self.sender.header_from_email,
                    header_from_name=self.sender.header_from_name,
                    reply_to=self.sender.reply_to,
                    message_id=message_id,
                    content_hash=draft.content_hash,
                    transport_name=self.sender.transport_name,
                    security_mode=self.sender.security_mode.value,
                    created_at=created_at,
                )
            )
            attempt_id = attempt.id
            draft_id = draft.id
        except EmailDeliveryServiceError:
            self.session.rollback()
            raise
        except IntegrityError:
            self.session.rollback()
            raise EmailDeliveryAlreadyAttemptedError() from None
        except SQLAlchemyError:
            self.session.rollback()
            raise EmailDeliveryPersistenceRecoveryRequiredError(_RECOVERY) from None
        except Exception:
            self.session.rollback()
            raise EmailDeliveryInternalError(_INTERNAL) from None

        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise EmailDeliveryAlreadyAttemptedError() from None
        except Exception:
            self.session.rollback()
            raise EmailDeliveryPersistenceRecoveryRequiredError(_RECOVERY) from None

        try:
            receipt = self.transport.send(envelope)
        except SMTPTransportError as error:
            return self._persist_transport_failure(attempt_id, draft_id, error)
        except Exception:
            self._persist_unknown(attempt_id, draft_id, "internal")
            raise EmailDeliveryUnknownOutcomeError(_UNKNOWN) from None

        validated_receipt = self._validated_receipt(receipt)
        if validated_receipt is None:
            self._persist_unknown(attempt_id, draft_id, "receipt_invalid")
            raise EmailDeliveryUnknownOutcomeError(_UNKNOWN)
        if (
            validated_receipt.recipient != envelope.envelope_to
            or validated_receipt.message_id != envelope.message_id
            or validated_receipt.provider != self.sender.transport_name
            or validated_receipt.security_mode is not self.sender.security_mode
        ):
            self._persist_unknown(attempt_id, draft_id, "receipt_mismatch")
            raise EmailDeliveryUnknownOutcomeError(_UNKNOWN)
        completed_at = _utc(self.clock())
        return self._transition(
            attempt_id,
            draft_id,
            EmailDeliveryAttemptOutcomeUpdate(
                outcome=EmailDeliveryOutcome.ACCEPTED,
                smtp_classification=None,
                smtp_code=validated_receipt.smtp_code,
                error_category=None,
                completed_at=completed_at,
                accepted_at=completed_at,
                unknown_at=None,
            ),
        )

    @staticmethod
    def _validated_command(command: object) -> ConfirmedEmailSendCommand:
        if type(command) is not ConfirmedEmailSendCommand:
            raise EmailDeliveryInvalidCommandError(_INVALID)
        if type(command.confirmed) is not bool or command.confirmed is not True:
            raise EmailDeliveryConfirmationRequiredError(_CONFIRMATION)
        try:
            return ConfirmedEmailSendCommand(**command.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise EmailDeliveryInvalidCommandError(_INVALID) from None

    def _load_authoritative_draft(self, data: ConfirmedEmailSendCommand) -> EmailDraft:
        self.session.expire_all()
        draft: EmailDraft | None = self.session.get(
            EmailDraft, data.email_draft_id, populate_existing=True
        )
        if (
            draft is None
            or draft.project_id != data.project_id
            or draft.company_id != data.company_id
            or draft.contact_id != data.contact_id
        ):
            raise EmailDeliveryNotFoundError(_NOT_FOUND)
        project = self.session.get(Project, draft.project_id, populate_existing=True)
        company = self.session.get(Company, draft.company_id, populate_existing=True)
        contact = self.session.get(Contact, draft.contact_id, populate_existing=True)
        lead = self.session.get(Lead, draft.lead_id, populate_existing=True)
        task = self.session.get(Task, draft.task_id, populate_existing=True)
        if project is None or company is None or contact is None or lead is None or task is None:
            raise EmailDeliveryNotFoundError(_NOT_FOUND)
        if (
            company.project_id != project.id
            or contact.company_id != company.id
            or lead.company_id != company.id
            or lead.contact_id != contact.id
            or task.lead_id != lead.id
        ):
            raise EmailDeliveryNotFoundError(_NOT_FOUND)
        if task.status not in {
            TaskLifecycleStatus.TODO.value,
            TaskLifecycleStatus.IN_PROGRESS.value,
        }:
            raise EmailDeliveryStaleContextError(_STALE)
        if draft.status != EmailDraftStatus.APPROVED.value:
            raise EmailDeliveryNotApprovedError(_NOT_APPROVED)
        try:
            current_email = (
                normalize_discovered_email(contact.email) if type(contact.email) is str else None
            )
        except (TypeError, ValueError):
            current_email = None
        if current_email is None or current_email != draft.recipient_email:
            raise EmailDeliveryStaleContextError(_STALE)
        expected_hash = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=draft.text_body,
            prompt_version=draft.prompt_version,
        )
        if expected_hash != draft.content_hash:
            raise EmailDeliveryStaleContextError(_STALE)
        if self.sender.header_from_name != draft.sender_name:
            raise EmailDeliveryStaleContextError(_STALE)
        return draft

    def _envelope(
        self, draft: EmailDraft, message_id: str, created_at: datetime
    ) -> SMTPMessageEnvelope:
        try:
            return SMTPMessageEnvelope(
                envelope_from=self.sender.envelope_from,
                envelope_to=draft.recipient_email,
                sender=SMTPSenderIdentity(
                    email=self.sender.header_from_email,
                    display_name=self.sender.header_from_name,
                    reply_to=self.sender.reply_to,
                ),
                subject=draft.subject,
                text_body=draft.text_body,
                message_id=message_id,
                date=created_at,
            )
        except (ValidationError, TypeError, ValueError):
            raise EmailDeliveryConfigurationError(_CONFIGURATION) from None

    @staticmethod
    def _validated_receipt(receipt: object) -> SMTPDeliveryReceipt | None:
        if type(receipt) is not SMTPDeliveryReceipt:
            return None
        try:
            return SMTPDeliveryReceipt(**receipt.model_dump())
        except (ValidationError, TypeError, ValueError):
            return None

    def _persist_transport_failure(
        self, attempt_id: int, draft_id: int, error: SMTPTransportError
    ) -> ConfirmedEmailSendResult:
        category = self._error_category(error)
        if error.classification is SMTPFailureClassification.TRANSIENT:
            outcome = EmailDeliveryOutcome.TRANSIENT_FAILURE
            classification = EmailDeliverySMTPClassification.TRANSIENT
            domain_error: type[EmailDeliveryServiceError] = EmailDeliveryTransientFailureError
            message = _TRANSIENT
        elif error.classification is SMTPFailureClassification.PERMANENT:
            outcome = EmailDeliveryOutcome.PERMANENT_FAILURE
            classification = EmailDeliverySMTPClassification.PERMANENT
            domain_error = EmailDeliveryPermanentFailureError
            message = _PERMANENT
        else:
            outcome = EmailDeliveryOutcome.UNKNOWN
            classification = EmailDeliverySMTPClassification.UNKNOWN
            domain_error = EmailDeliveryUnknownOutcomeError
            message = _UNKNOWN
        completed_at = _utc(self.clock())
        self._transition(
            attempt_id,
            draft_id,
            EmailDeliveryAttemptOutcomeUpdate(
                outcome=outcome,
                smtp_classification=classification,
                smtp_code=_safe_smtp_code(error.smtp_code),
                error_category=category,
                completed_at=completed_at,
                accepted_at=None,
                unknown_at=(completed_at if outcome is EmailDeliveryOutcome.UNKNOWN else None),
            ),
        )
        raise domain_error(message) from None

    def _persist_unknown(
        self, attempt_id: int, draft_id: int, category: str
    ) -> ConfirmedEmailSendResult:
        completed_at = _utc(self.clock())
        return self._transition(
            attempt_id,
            draft_id,
            EmailDeliveryAttemptOutcomeUpdate(
                outcome=EmailDeliveryOutcome.UNKNOWN,
                smtp_classification=EmailDeliverySMTPClassification.UNKNOWN,
                smtp_code=None,
                error_category=category,
                completed_at=completed_at,
                accepted_at=None,
                unknown_at=completed_at,
            ),
        )

    def _transition(
        self,
        attempt_id: int,
        draft_id: int,
        update: EmailDeliveryAttemptOutcomeUpdate,
    ) -> ConfirmedEmailSendResult:
        try:
            self.session.expire_all()
            current = self.repository.get(attempt_id)
            if current is None or current.outcome != EmailDeliveryOutcome.RESERVED.value:
                raise EmailDeliveryPersistenceRecoveryRequiredError(_RECOVERY)
            attempt = self.repository.transition(attempt_id, update)
            result = self._result(draft_id, attempt)
            self.session.commit()
            return result
        except EmailDeliveryPersistenceRecoveryRequiredError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise EmailDeliveryPersistenceRecoveryRequiredError(_RECOVERY) from None

    @staticmethod
    def _result(draft_id: int, attempt: EmailDeliveryAttempt) -> ConfirmedEmailSendResult:
        try:
            return ConfirmedEmailSendResult(
                email_draft_id=draft_id,
                delivery_attempt_id=attempt.id,
                recipient=attempt.recipient_email,
                message_id=attempt.message_id,
                outcome=EmailDeliveryOutcome(attempt.outcome),
                created_at=_database_utc(attempt.created_at),
                completed_at=_database_utc(attempt.completed_at),
                accepted_at=(
                    None if attempt.accepted_at is None else _database_utc(attempt.accepted_at)
                ),
                smtp_classification=(
                    None
                    if attempt.smtp_classification is None
                    else EmailDeliverySMTPClassification(attempt.smtp_classification)
                ),
                smtp_code=attempt.smtp_code,
                error_category=attempt.error_category,
            )
        except (ValidationError, TypeError, ValueError):
            raise EmailDeliveryPersistenceRecoveryRequiredError(_RECOVERY) from None

    @staticmethod
    def _error_category(error: SMTPTransportError) -> str:
        if isinstance(error, SMTPConfigurationError):
            return "configuration"
        if isinstance(error, SMTPConnectionFailedError):
            return "connection"
        if isinstance(error, SMTPTLSUnavailableError | SMTPTLSNegotiationError):
            return "tls"
        if isinstance(error, SMTPAuthenticationFailedError):
            return "authentication"
        if isinstance(error, SMTPSenderRejectedError):
            return "sender"
        if isinstance(error, SMTPRecipientRejectedError):
            return "recipient"
        if isinstance(error, SMTPDataRejectedError):
            return "data"
        if isinstance(error, SMTPProtocolError):
            return "protocol"
        if isinstance(error, SMTPTimeoutError):
            return "timeout"
        if isinstance(error, SMTPDeliveryOutcomeUnknownError):
            return "unknown"
        if isinstance(error, SMTPInternalError):
            return "internal"
        return "unknown"

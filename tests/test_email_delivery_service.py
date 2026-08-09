from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryConfigurationError,
    EmailDeliveryConfirmationRequiredError,
    EmailDeliveryNotApprovedError,
    EmailDeliveryStaleContextError,
    EmailDeliveryUnknownOutcomeError,
    TrustedEmailSenderConfig,
    _database_utc,
    build_delivery_attempt_key,
    build_delivery_message_id,
)
from app.modules.email_draft.context import build_content_hash
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task, TaskLifecycleStatus
from app.providers.smtp.contracts import (
    SMTPDeliveryReceipt,
    SMTPMessageEnvelope,
    SMTPSecurityMode,
)

NOW = datetime(2026, 8, 9, 14, tzinfo=UTC)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[SMTPMessageEnvelope] = []
        self.receipt_recipient: str | None = None
        self.receipt_message_id: str | None = None

    def send(self, message: SMTPMessageEnvelope) -> SMTPDeliveryReceipt:
        self.calls.append(message)
        return SMTPDeliveryReceipt(
            accepted=True,
            recipient=self.receipt_recipient or message.envelope_to,
            message_id=self.receipt_message_id or str(message.message_id),
            smtp_code=250,
            provider="fake-smtp",
            security_mode=SMTPSecurityMode.STARTTLS,
        )


def _sender(**changes: object) -> TrustedEmailSenderConfig:
    values: dict[str, object] = {
        "envelope_from": "bounce@example.test",
        "header_from_email": "sender@example.test",
        "header_from_name": "Alex Sender",
        "reply_to": "reply@example.test",
        "message_id_domain": "mail.example.test",
        "transport_name": "fake-smtp",
        "security_mode": SMTPSecurityMode.STARTTLS,
    }
    values.update(changes)
    return TrustedEmailSenderConfig(**values)


def _records(status: EmailDraftStatus = EmailDraftStatus.APPROVED) -> tuple[int, int, int, int]:
    with SessionLocal() as session:
        project = Project(name="Project")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Company")
        session.add(company)
        session.flush()
        contact = Contact(
            company_id=company.id,
            first_name="Recipient",
            email="recipient@example.test",
        )
        session.add(contact)
        session.flush()
        lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW")
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id,
            title="Send outreach",
            status=TaskLifecycleStatus.TODO.value,
        )
        session.add(task)
        session.flush()
        content_hash = build_content_hash(
            recipient_email="recipient@example.test",
            subject="Reviewed subject",
            text_body="Reviewed plain-text body with sufficient stable content.",
            prompt_version="email-outreach-draft-v1",
        )
        draft = EmailDraft(
            project_id=project.id,
            company_id=company.id,
            contact_id=contact.id,
            lead_id=lead.id,
            task_id=task.id,
            recipient_email="recipient@example.test",
            recipient_name="Recipient",
            recipient_role=None,
            sender_name="Alex Sender",
            sender_company="Sender Company",
            generation_tone="professional",
            generation_purpose="Outreach",
            generation_value_proposition=None,
            subject="Reviewed subject",
            text_body="Reviewed plain-text body with sufficient stable content.",
            language="en",
            prompt_version="email-outreach-draft-v1",
            provider="fake",
            model="fake-model",
            context_fingerprint="a" * 64,
            request_fingerprint="b" * 64,
            content_hash=content_hash,
            status=status.value,
            reviewed_at=NOW if status is not EmailDraftStatus.DRAFT else None,
            approved_at=NOW if status is EmailDraftStatus.APPROVED else None,
            rejected_at=NOW if status is EmailDraftStatus.REJECTED else None,
        )
        session.add(draft)
        session.commit()
        return project.id, company.id, contact.id, draft.id


def _command(ids: tuple[int, int, int, int]) -> ConfirmedEmailSendCommand:
    project_id, company_id, contact_id, draft_id = ids
    return ConfirmedEmailSendCommand(
        project_id=project_id,
        company_id=company_id,
        contact_id=contact_id,
        email_draft_id=draft_id,
        confirmed=True,
    )


def _service(session: object, transport: RecordingTransport, sender=None):
    return ConfirmedEmailSendService(
        session=session,
        repository=EmailDeliveryAttemptRepository(session),
        transport=transport,
        sender=sender or _sender(),
        clock=lambda: NOW,
    )


def test_confirmed_approved_send_persists_one_accepted_attempt() -> None:
    ids = _records()
    transport = RecordingTransport()
    with SessionLocal() as session:
        before = session.get(EmailDraft, ids[3])
        assert before is not None
        snapshot = (
            before.status,
            before.subject,
            before.text_body,
            before.recipient_email,
            before.content_hash,
            before.context_fingerprint,
            before.prompt_version,
            before.reviewed_at,
            before.approved_at,
        )
        session.commit()
        result = _service(session, transport).send(_command(ids))
        attempt = session.scalar(select(EmailDeliveryAttempt))
        draft = session.get(EmailDraft, ids[3])
        assert attempt is not None
        assert draft is not None
        assert result.outcome is EmailDeliveryOutcome.ACCEPTED
        assert attempt.outcome == EmailDeliveryOutcome.ACCEPTED.value
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1
        assert len(transport.calls) == 1
        envelope = transport.calls[0]
        assert envelope.envelope_to == before.recipient_email
        assert envelope.subject == before.subject
        assert envelope.text_body == before.text_body
        assert envelope.message_id == attempt.message_id
        assert envelope.date == NOW
        assert (
            draft.status,
            draft.subject,
            draft.text_body,
            draft.recipient_email,
            draft.content_hash,
            draft.context_fingerprint,
            draft.prompt_version,
            draft.reviewed_at,
            draft.approved_at,
        ) == snapshot


def test_sqlite_hydrated_naive_result_timestamps_are_interpreted_as_utc() -> None:
    naive = datetime(2026, 1, 1, 12)
    assert _database_utc(naive) == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_aware_database_timestamp_normalizes_the_same_instant_to_utc() -> None:
    from datetime import timedelta, timezone

    source = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert _database_utc(source) == datetime(2026, 1, 1, 6, 30, tzinfo=UTC)


def test_naive_injected_clock_remains_invalid_before_reservation_or_send() -> None:
    ids = _records()
    transport = RecordingTransport()
    with SessionLocal() as session:
        service = ConfirmedEmailSendService(
            session=session,
            repository=EmailDeliveryAttemptRepository(session),
            transport=transport,
            sender=_sender(),
            clock=lambda: datetime(2026, 1, 1, 12),
        )
        with pytest.raises(EmailDeliveryConfigurationError):
            service.send(_command(ids))
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_unconfirmed_command_is_rejected_before_reservation_or_send() -> None:
    ids = _records()
    command = ConfirmedEmailSendCommand.model_construct(
        project_id=ids[0],
        company_id=ids[1],
        contact_id=ids[2],
        email_draft_id=ids[3],
        confirmed=False,
    )
    transport = RecordingTransport()
    with SessionLocal() as session:
        with pytest.raises(EmailDeliveryConfirmationRequiredError):
            _service(session, transport).send(command)
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


@pytest.mark.parametrize("status", [EmailDraftStatus.DRAFT, EmailDraftStatus.REJECTED])
def test_nonapproved_drafts_are_blocked(status: EmailDraftStatus) -> None:
    ids = _records(status)
    transport = RecordingTransport()
    with SessionLocal() as session:
        with pytest.raises(EmailDeliveryNotApprovedError):
            _service(session, transport).send(_command(ids))
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_changed_authoritative_recipient_is_blocked() -> None:
    ids = _records()
    with SessionLocal() as session:
        contact = session.get(Contact, ids[2])
        assert contact is not None
        contact.email = "changed@example.test"
        session.commit()
    transport = RecordingTransport()
    with SessionLocal() as session:
        with pytest.raises(EmailDeliveryStaleContextError):
            _service(session, transport).send(_command(ids))
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_reviewed_sender_mismatch_is_blocked() -> None:
    ids = _records()
    transport = RecordingTransport()
    with SessionLocal() as session, pytest.raises(EmailDeliveryStaleContextError):
        _service(session, transport, _sender(header_from_name="Other Sender")).send(_command(ids))
    assert transport.calls == []


def test_repeated_send_keeps_one_attempt_and_one_transport_call() -> None:
    ids = _records()
    transport = RecordingTransport()
    with SessionLocal() as session:
        service = _service(session, transport)
        service.send(_command(ids))
        with pytest.raises(EmailDeliveryAlreadyAttemptedError) as captured:
            service.send(_command(ids))
        assert captured.value.outcome == EmailDeliveryOutcome.ACCEPTED.value
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "domain",
    ["", "localhost", "méil.example", "mail..example", "mail example", "mail\r.example"],
)
def test_invalid_trusted_message_id_domains_are_rejected(domain: str) -> None:
    with pytest.raises(ValidationError):
        _sender(message_id_domain=domain)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("envelope_from", "invalid"),
        ("header_from_email", "invalid"),
        ("reply_to", "invalid"),
        ("header_from_name", "Injected\r\nBcc: x@example.test"),
    ],
)
def test_invalid_sender_configuration_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _sender(**{field: value})


def test_attempt_key_and_message_id_are_deterministic() -> None:
    arguments = {
        "project_id": 1,
        "email_draft_id": 2,
        "content_hash": "a" * 64,
        "recipient_email": "recipient@example.test",
        "message_id_domain": "mail.example.test",
    }
    first = build_delivery_attempt_key(**arguments)
    second = build_delivery_attempt_key(**arguments)
    different = build_delivery_attempt_key(**{**arguments, "email_draft_id": 3})
    assert first == second
    assert first != different
    assert build_delivery_message_id(first, "mail.example.test") == build_delivery_message_id(
        second, "mail.example.test"
    )


@pytest.mark.parametrize(
    "receipt_change",
    [
        {"receipt_recipient": "other@example.test"},
        {"receipt_message_id": "<other@mail.example.test>"},
    ],
)
def test_receipt_identity_mismatch_persists_unknown(receipt_change: dict[str, str]) -> None:
    ids = _records()
    transport = RecordingTransport()
    for name, value in receipt_change.items():
        setattr(transport, name, value)
    with SessionLocal() as session:
        with pytest.raises(EmailDeliveryUnknownOutcomeError):
            _service(session, transport).send(_command(ids))
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.UNKNOWN.value
        assert attempt.error_category == "receipt_mismatch"

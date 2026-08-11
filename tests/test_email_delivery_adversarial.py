from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.modules.contact.models import Contact
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryPermanentFailureError,
    EmailDeliveryPersistenceRecoveryRequiredError,
    EmailDeliveryStaleContextError,
    EmailDeliveryTransactionBoundaryError,
    EmailDeliveryTransientFailureError,
    EmailDeliveryUnknownOutcomeError,
)
from app.modules.email_draft.models import EmailDraft
from app.modules.task.models import Task, TaskLifecycleStatus
from app.providers.smtp.contracts import (
    SMTPDeliveryReceipt,
    SMTPMessageEnvelope,
    SMTPSecurityMode,
)
from app.providers.smtp.errors import (
    SMTPAuthenticationFailedError,
    SMTPConnectionFailedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPTransportError,
)
from app.providers.smtp.fake import FakeSMTPScenario, FakeSMTPTransport

from .test_email_delivery_service import NOW, _command, _records, _sender, _service

_FAILURE_CASES = [
    (
        SMTPConnectionFailedError,
        EmailDeliveryTransientFailureError,
        EmailDeliveryOutcome.TRANSIENT_FAILURE,
        "TRANSIENT",
        "connection",
    ),
    (
        SMTPAuthenticationFailedError,
        EmailDeliveryPermanentFailureError,
        EmailDeliveryOutcome.PERMANENT_FAILURE,
        "PERMANENT",
        "authentication",
    ),
    (
        SMTPDeliveryOutcomeUnknownError,
        EmailDeliveryUnknownOutcomeError,
        EmailDeliveryOutcome.UNKNOWN,
        "UNKNOWN",
        "unknown",
    ),
]


class CodeFailureTransport:
    def __init__(self, error: SMTPTransportError) -> None:
        self.error = error
        self.calls: list[SMTPMessageEnvelope] = []

    def send(self, message: SMTPMessageEnvelope) -> SMTPDeliveryReceipt:
        self.calls.append(message)
        raise self.error


class SMTPCodeInt(int):
    pass


@pytest.mark.parametrize(
    "smtp_code",
    [-1, 0, 99, 100, 199, 600, 999, True, False, "250", None, SMTPCodeInt(250)],
)
@pytest.mark.parametrize(
    ("error_type", "domain_error", "outcome", "classification", "category"),
    _FAILURE_CASES,
    ids=["transient", "permanent", "unknown"],
)
def test_invalid_smtp_codes_are_sanitized_without_reclassification(
    smtp_code: object,
    error_type: type[SMTPTransportError],
    domain_error: type[Exception],
    outcome: EmailDeliveryOutcome,
    classification: str,
    category: str,
) -> None:
    ids = _records()
    transport = CodeFailureTransport(error_type(smtp_code=smtp_code))
    with SessionLocal() as session, pytest.raises(domain_error) as captured:
        _service(session, transport).send(_command(ids))
    assert "ValidationError" not in type(captured.value).__name__
    assert "SMTP code" not in str(captured.value)
    with SessionLocal() as fresh:
        attempt = fresh.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.smtp_code is None
        assert attempt.outcome == outcome.value
        assert attempt.smtp_classification == classification
        assert attempt.error_category == category
        assert attempt.completed_at is not None
        assert attempt.row_version == 2
    assert len(transport.calls) == 1


@pytest.mark.parametrize("smtp_code", [200, 250, 421, 450, 500, 550, 599])
@pytest.mark.parametrize(
    ("error_type", "domain_error", "outcome", "classification", "category"),
    _FAILURE_CASES,
    ids=["transient", "permanent", "unknown"],
)
def test_valid_smtp_codes_are_preserved_without_reclassification(
    smtp_code: int,
    error_type: type[SMTPTransportError],
    domain_error: type[Exception],
    outcome: EmailDeliveryOutcome,
    classification: str,
    category: str,
) -> None:
    ids = _records()
    transport = CodeFailureTransport(error_type(smtp_code=smtp_code))
    with SessionLocal() as session, pytest.raises(domain_error):
        _service(session, transport).send(_command(ids))
    with SessionLocal() as fresh:
        attempt = fresh.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.smtp_code == smtp_code
        assert attempt.outcome == outcome.value
        assert attempt.smtp_classification == classification
        assert attempt.error_category == category
        assert attempt.completed_at is not None
        assert attempt.row_version == 2
    assert len(transport.calls) == 1


class AdversarialReceiptTransport:
    def __init__(self, receipt: object) -> None:
        self.receipt = receipt
        self.calls: list[SMTPMessageEnvelope] = []

    def send(self, message: SMTPMessageEnvelope) -> object:
        self.calls.append(message)
        if callable(self.receipt):
            return self.receipt(message)
        return self.receipt


def _receipt(message: SMTPMessageEnvelope, **changes: object) -> SMTPDeliveryReceipt:
    values: dict[str, object] = {
        "accepted": True,
        "recipient": message.envelope_to,
        "message_id": message.message_id,
        "smtp_code": 250,
        "provider": "fake-smtp",
        "security_mode": SMTPSecurityMode.STARTTLS,
    }
    values.update(changes)
    return SMTPDeliveryReceipt(**values)


@pytest.mark.parametrize(
    "receipt_factory",
    [
        lambda message: _receipt(message, recipient="other@example.test"),
        lambda message: _receipt(message, message_id="<other@mail.example.test>"),
        lambda message: _receipt(message, provider="other-smtp"),
        lambda message: _receipt(message, security_mode=SMTPSecurityMode.TLS_IMPLICIT),
        lambda _message: {"accepted": True},
        lambda _message: object(),
        lambda _message: None,
    ],
    ids=[
        "wrong-recipient",
        "wrong-message-id",
        "wrong-provider",
        "wrong-security",
        "mapping",
        "unexpected-object",
        "missing-receipt",
    ],
)
def test_receipt_identity_matrix_is_conservatively_unknown(receipt_factory) -> None:
    ids = _records()
    transport = AdversarialReceiptTransport(receipt_factory)
    with SessionLocal() as session, pytest.raises(EmailDeliveryUnknownOutcomeError):
        _service(session, transport).send(_command(ids))
    with SessionLocal() as fresh:
        attempt = fresh.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.UNKNOWN.value
        fresh.rollback()
        second = FakeSMTPTransport()
        with pytest.raises(EmailDeliveryAlreadyAttemptedError):
            _service(fresh, second).send(_command(ids))
        assert second.calls == []
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "domain",
    [
        "mail..example.test",
        ".mail.example.test",
        "mail.example.test.",
        "localhost",
        "m\u00e4il.example.test",
        "mail example.test",
        "mail\r\n.example.test",
        f"{'a' * 64}.example.test",
        f"{'a' * 250}.test",
    ],
)
def test_adversarial_message_id_domains_are_rejected(domain: str) -> None:
    with pytest.raises(ValidationError):
        _sender(message_id_domain=domain)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("header_from_name", "Sender\rBcc: victim@example.test"),
        ("header_from_name", "Sender\nBcc: victim@example.test"),
        ("header_from_name", "Sender\r\nBcc: victim@example.test"),
        ("reply_to", "reply@example.test\r\nBcc: victim@example.test"),
        ("envelope_from", "bounce@example.test\nRCPT TO:victim@example.test"),
        ("transport_name", "smtp\x00hidden"),
    ],
)
def test_sender_header_injection_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _sender(**{field: value})


def test_select_owned_transaction_blocks_before_database_or_smtp() -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Contact)) == 1
        with pytest.raises(EmailDeliveryTransactionBoundaryError):
            _service(session, transport).send(_command(ids))
        session.rollback()
    assert transport.calls == []


def test_unrelated_pending_mutation_is_never_committed_by_send() -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:
        contact = session.get(Contact, ids[2])
        assert contact is not None
        original = contact.first_name
        contact.first_name = "Uncommitted mutation"
        with pytest.raises(EmailDeliveryTransactionBoundaryError):
            _service(session, transport).send(_command(ids))
        session.rollback()
    with SessionLocal() as fresh:
        contact = fresh.get(Contact, ids[2])
        assert contact is not None
        assert contact.first_name == original
        assert fresh.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_reservation_flush_failure_rolls_back_without_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:
        repository = EmailDeliveryAttemptRepository(session)

        def fail_reserve(_data: object) -> None:
            raise SQLAlchemyError("SUPER_SECRET_SMTP_PASSWORD")

        monkeypatch.setattr(repository, "reserve", fail_reserve)
        service = ConfirmedEmailSendService(
            session=session,
            repository=repository,
            transport=transport,
            sender=_sender(),
            clock=lambda: NOW,
        )
        with pytest.raises(EmailDeliveryPersistenceRecoveryRequiredError) as captured:
            service.send(_command(ids))
        assert "SUPER_SECRET_SMTP_PASSWORD" not in str(captured.value)
        assert not session.in_transaction()
    assert transport.calls == []


@pytest.mark.parametrize(
    "scenario",
    [
        FakeSMTPScenario.SUCCESS,
        FakeSMTPScenario.CONNECTION_FAILURE,
        FakeSMTPScenario.AUTH_FAILURE,
        FakeSMTPScenario.UNKNOWN_OUTCOME,
    ],
)
def test_every_tx2_failure_keeps_reserved_and_blocks_fresh_session_resend(
    monkeypatch: pytest.MonkeyPatch, scenario: FakeSMTPScenario
) -> None:
    ids = _records()
    transport = FakeSMTPTransport(scenario=scenario)
    with SessionLocal() as session:
        real_commit = session.commit
        calls = 0

        def fail_second_commit() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SQLAlchemyError("SUPER_SECRET_SMTP_PASSWORD")
            real_commit()

        monkeypatch.setattr(session, "commit", fail_second_commit)
        with pytest.raises(EmailDeliveryPersistenceRecoveryRequiredError) as captured:
            _service(session, transport).send(_command(ids))
        assert "SUPER_SECRET_SMTP_PASSWORD" not in repr(captured.value)
    with SessionLocal() as fresh:
        attempt = fresh.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.RESERVED.value
        fresh.rollback()
        second = FakeSMTPTransport()
        with pytest.raises(EmailDeliveryAlreadyAttemptedError):
            _service(fresh, second).send(_command(ids))
        assert second.calls == []
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("task_status", TaskLifecycleStatus.DONE.value),
        ("recipient", "recipient@ex\u0430mple.test"),
        ("subject", "Tampered reviewed subject"),
    ],
)
def test_authoritative_context_tampering_blocks_before_reservation(
    mutation: str, value: str
) -> None:
    ids = _records()
    with SessionLocal() as session:
        draft = session.get(EmailDraft, ids[3])
        assert draft is not None
        if mutation == "task_status":
            task = session.get(Task, draft.task_id)
            assert task is not None
            task.status = value
        elif mutation == "recipient":
            contact = session.get(Contact, ids[2])
            assert contact is not None
            contact.email = value
        else:
            session.execute(
                EmailDraft.__table__.update().where(EmailDraft.id == draft.id).values(subject=value)
            )
        session.commit()
    transport = FakeSMTPTransport()
    with SessionLocal() as fresh:
        with pytest.raises((EmailDeliveryStaleContextError, ValueError)):
            _service(fresh, transport).send(_command(ids))
        assert fresh.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_nested_transaction_is_rejected_without_committing_savepoint_work() -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:
        with session.begin_nested(), pytest.raises(EmailDeliveryTransactionBoundaryError):
            _service(session, transport).send(_command(ids))
        session.rollback()
    with SessionLocal() as fresh:
        assert fresh.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_clock_failure_is_sanitized_before_reservation() -> None:
    ids = _records()
    transport = FakeSMTPTransport()

    def fail_clock() -> datetime:
        raise RuntimeError("SUPER_SECRET_SMTP_PASSWORD C:/internal/path")

    with SessionLocal() as session:
        service = ConfirmedEmailSendService(
            session=session,
            repository=EmailDeliveryAttemptRepository(session),
            transport=transport,
            sender=_sender(),
            clock=fail_clock,
        )
        with pytest.raises(ValueError) as captured:
            service.send(_command(ids))
        assert "SUPER_SECRET_SMTP_PASSWORD" not in str(captured.value)
        assert "C:/internal/path" not in repr(captured.value)
    assert transport.calls == []

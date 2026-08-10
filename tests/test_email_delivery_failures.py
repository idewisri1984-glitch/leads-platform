import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryPermanentFailureError,
    EmailDeliveryPersistenceRecoveryRequiredError,
    EmailDeliveryTransactionBoundaryError,
    EmailDeliveryTransientFailureError,
    EmailDeliveryUnknownOutcomeError,
)
from app.providers.smtp.fake import FakeSMTPScenario, FakeSMTPTransport

from .test_email_delivery_service import NOW, _command, _records, _sender


def _service(session, transport):
    return ConfirmedEmailSendService(
        session=session,
        repository=EmailDeliveryAttemptRepository(session),
        transport=transport,
        sender=_sender(),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("scenario", "error", "outcome", "category"),
    [
        (
            FakeSMTPScenario.CONNECTION_FAILURE,
            EmailDeliveryTransientFailureError,
            EmailDeliveryOutcome.TRANSIENT_FAILURE,
            "connection",
        ),
        (
            FakeSMTPScenario.AUTH_FAILURE,
            EmailDeliveryPermanentFailureError,
            EmailDeliveryOutcome.PERMANENT_FAILURE,
            "authentication",
        ),
        (
            FakeSMTPScenario.UNKNOWN_OUTCOME,
            EmailDeliveryUnknownOutcomeError,
            EmailDeliveryOutcome.UNKNOWN,
            "unknown",
        ),
    ],
)
def test_transport_failures_persist_safe_terminal_outcome_without_retry(
    scenario, error, outcome, category
) -> None:
    ids = _records()
    transport = FakeSMTPTransport(scenario=scenario)
    with SessionLocal() as session:
        service = _service(session, transport)
        with pytest.raises(error):
            service.send(_command(ids))
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == outcome.value
        assert attempt.error_category == category
        session.rollback()
        with pytest.raises(EmailDeliveryAlreadyAttemptedError):
            service.send(_command(ids))
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1
    assert len(transport.calls) == 1


def test_tx1_commit_failure_prevents_transport_call(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:

        def fail_commit() -> None:
            raise SQLAlchemyError("SUPER_SECRET_SMTP_PASSWORD")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(EmailDeliveryPersistenceRecoveryRequiredError) as captured:
            _service(session, transport).send(_command(ids))
        assert "SUPER_SECRET_SMTP_PASSWORD" not in str(captured.value)
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_service_rejects_a_genuinely_caller_owned_active_transaction() -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
        assert session.in_transaction()
        with pytest.raises(EmailDeliveryTransactionBoundaryError):
            _service(session, transport).send(_command(ids))
        session.rollback()
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_service_rejects_mismatched_repository_session_before_side_effects() -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as service_session, SessionLocal() as repository_session:
        with pytest.raises(EmailDeliveryTransactionBoundaryError):
            ConfirmedEmailSendService(
                session=service_session,
                repository=EmailDeliveryAttemptRepository(repository_session),
                transport=transport,
                sender=_sender(),
                clock=lambda: NOW,
            )
        assert not service_session.in_transaction()
        assert not repository_session.in_transaction()
        service_session.rollback()
        repository_session.rollback()
    with SessionLocal() as verification_session:
        assert verification_session.get(EmailDeliveryAttempt, ids[3]) is None
        assert (
            verification_session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
        )
    assert transport.calls == []


def test_same_bind_different_repository_session_is_rejected() -> None:
    transport = FakeSMTPTransport()
    with SessionLocal() as service_session, SessionLocal() as repository_session:
        assert service_session is not repository_session
        assert service_session.get_bind() is repository_session.get_bind()
        with pytest.raises(EmailDeliveryTransactionBoundaryError):
            ConfirmedEmailSendService(
                session=service_session,
                repository=EmailDeliveryAttemptRepository(repository_session),
                transport=transport,
                sender=_sender(),
                clock=lambda: NOW,
            )
        assert not service_session.in_transaction()
        assert not repository_session.in_transaction()
    assert transport.calls == []


def test_accepted_tx2_commit_failure_keeps_reservation_and_blocks_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    with SessionLocal() as session:
        real_commit = session.commit
        count = 0

        def fail_second_commit() -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise SQLAlchemyError("terminal commit failed")
            real_commit()

        monkeypatch.setattr(session, "commit", fail_second_commit)
        service = _service(session, transport)
        with pytest.raises(EmailDeliveryPersistenceRecoveryRequiredError):
            service.send(_command(ids))
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.RESERVED.value
        session.rollback()
        with pytest.raises(EmailDeliveryAlreadyAttemptedError):
            service.send(_command(ids))
    assert len(transport.calls) == 1


def test_failure_tx2_commit_failure_keeps_reservation_and_blocks_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    transport = FakeSMTPTransport(scenario=FakeSMTPScenario.CONNECTION_FAILURE)
    with SessionLocal() as session:
        real_commit = session.commit
        count = 0

        def fail_second_commit() -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise SQLAlchemyError("terminal commit failed")
            real_commit()

        monkeypatch.setattr(session, "commit", fail_second_commit)
        service = _service(session, transport)
        with pytest.raises(EmailDeliveryPersistenceRecoveryRequiredError):
            service.send(_command(ids))
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.RESERVED.value
        session.rollback()
        with pytest.raises(EmailDeliveryAlreadyAttemptedError):
            service.send(_command(ids))
    assert len(transport.calls) == 1


class SecretFailureTransport:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, message):
        self.calls += 1
        raise RuntimeError("SUPER_SECRET_SMTP_PASSWORD")


def test_unexpected_transport_exception_is_sanitized_and_persisted_unknown() -> None:
    ids = _records()
    transport = SecretFailureTransport()
    with SessionLocal() as session:
        with pytest.raises(EmailDeliveryUnknownOutcomeError) as captured:
            _service(session, transport).send(_command(ids))
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.UNKNOWN.value
        assert attempt.error_category == "internal"
        assert "SUPER_SECRET_SMTP_PASSWORD" not in str(captured.value)
        assert "SUPER_SECRET_SMTP_PASSWORD" not in repr(captured.value)
        assert "SUPER_SECRET_SMTP_PASSWORD" not in repr(attempt.__dict__)
    assert transport.calls == 1

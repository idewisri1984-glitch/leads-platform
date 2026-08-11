from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.manual_repository import ManualEmailSendRecordRepository
from app.modules.email_delivery.manual_schemas import (
    ConfirmedManualEmailSendCommand,
    ManualEmailDraftScope,
    ManualOutreachStatus,
)
from app.modules.email_delivery.manual_service import (
    ManualOutreachAlreadySentError,
    ManualOutreachAutomaticAttemptError,
    ManualOutreachService,
    ManualOutreachStaleContextError,
)
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
)
from app.modules.email_draft.models import EmailDraft

from .test_email_delivery_service import RecordingTransport, _command, _records, _sender

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _scope(ids: dict[str, int]) -> ManualEmailDraftScope:
    command = _command(ids)
    return ManualEmailDraftScope(
        project_id=command.project_id,
        company_id=command.company_id,
        contact_id=command.contact_id,
        email_draft_id=command.email_draft_id,
    )


def _confirmed(ids: dict[str, int]) -> ConfirmedManualEmailSendCommand:
    return ConfirmedManualEmailSendCommand(**_scope(ids).model_dump(), confirmed=True)


def test_export_returns_exact_copy_package_without_persistence() -> None:
    ids = _records()
    with SessionLocal() as session:
        result = ManualOutreachService(
            session,
            ManualEmailSendRecordRepository(session),
            clock=lambda: NOW,
        ).export(_scope(ids))
        assert result.outreach_status is ManualOutreachStatus.READY_FOR_MANUAL_SEND
        assert result.recipient_email == "recipient@example.test"
        assert result.subject == "Reviewed subject"
        assert result.text_body == "Reviewed plain-text body with sufficient stable content."
        assert result.manual_send_record_id is None
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0


def test_mark_sent_persists_once_and_survives_a_new_session() -> None:
    ids = _records()
    with SessionLocal() as session:
        result = ManualOutreachService(
            session,
            ManualEmailSendRecordRepository(session),
            clock=lambda: NOW,
        ).mark_sent(_confirmed(ids))
        session.commit()
        assert result.outreach_status is ManualOutreachStatus.MANUALLY_SENT
        assert result.sent_at == NOW

    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        exported = service.export(_scope(ids))
        assert exported.outreach_status is ManualOutreachStatus.MANUALLY_SENT
        assert exported.manual_send_record_id == result.manual_send_record_id
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachAlreadySentError):
            service.mark_sent(_confirmed(ids))


def test_changed_contact_email_blocks_export_and_mark_sent() -> None:
    ids = _records()
    command = _command(ids)
    with SessionLocal() as session:
        contact = session.get(Contact, command.contact_id)
        assert contact is not None
        contact.email = "changed@example.test"
        session.commit()
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachStaleContextError):
            service.export(_scope(ids))


def test_manual_record_blocks_automatic_delivery_before_transport() -> None:
    ids = _records()
    with SessionLocal() as session:
        ManualOutreachService(
            session,
            ManualEmailSendRecordRepository(session),
            clock=lambda: NOW,
        ).mark_sent(_confirmed(ids))
        session.commit()
    transport = RecordingTransport()
    with (
        SessionLocal() as session,
        pytest.raises(EmailDeliveryAlreadyAttemptedError) as captured,
    ):
        ConfirmedEmailSendService(
            session=session,
            repository=EmailDeliveryAttemptRepository(session),
            transport=transport,
            sender=_sender(),
            clock=lambda: NOW,
        ).send(_command(ids))
    assert captured.value.outcome == "MANUAL"
    assert transport.calls == []
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0


def test_automatic_attempt_blocks_manual_export_and_mark_sent() -> None:
    ids = _records()
    transport = RecordingTransport()
    with SessionLocal() as session:
        ConfirmedEmailSendService(
            session=session,
            repository=EmailDeliveryAttemptRepository(session),
            transport=transport,
            sender=_sender(),
            clock=lambda: NOW,
        ).send(_command(ids))
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachAutomaticAttemptError):
            service.export(_scope(ids))
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachAutomaticAttemptError):
            service.mark_sent(_confirmed(ids))


def test_delivery_mode_database_constraint_rejects_unknown_value() -> None:
    ids = _records()
    command = _command(ids)
    with SessionLocal() as session:
        draft = session.get(EmailDraft, command.email_draft_id)
        assert draft is not None
        draft.delivery_mode = "UNSAFE"
        with pytest.raises(IntegrityError):
            session.commit()

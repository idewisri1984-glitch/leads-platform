import json
import smtplib
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, inspect, select, update
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.cli import email_draft as email_draft_cli
from app.cli.main import app
from app.core.database import Base, SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.manual_repository import ManualEmailSendRecordRepository
from app.modules.email_delivery.manual_schemas import ManualOutreachStatus
from app.modules.email_delivery.manual_service import (
    ManualOutreachNotApprovedError,
    ManualOutreachService,
    ManualOutreachStaleContextError,
)
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_delivery.service import ConfirmedEmailSendService
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task
from app.providers.smtp.client import SMTPClient

from .test_email_delivery_service import _command, _records
from .test_manual_outreach_cli import _arguments
from .test_manual_outreach_service import _confirmed, _scope

_SECRET = "SUPER_SECRET_SMTP_PASSWORD"
_PERSISTED_MODELS = (
    Project,
    Company,
    Contact,
    Lead,
    Task,
    EmailDraft,
    ManualEmailSendRecord,
    EmailDeliveryAttempt,
)


def _forbid_runtime_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("manual outreach crossed a runtime boundary")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(SMTPClient, "__init__", forbidden)
    monkeypatch.setattr(
        email_draft_cli._SMTPCompositionFactory,
        "__call__",
        forbidden,
    )
    monkeypatch.setattr(
        email_draft_cli._OpenAIGeneratorFactory,
        "__call__",
        forbidden,
    )
    monkeypatch.setattr(ConfirmedEmailSendService, "send", forbidden)


def _hostile_smtp_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid.example")
    monkeypatch.setenv("SMTP_PORT", "not-a-port")
    monkeypatch.setenv("SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("SMTP_PASSWORD", _SECRET)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:{_SECRET}@invalid/db")


def _state(session: Session) -> dict[str, tuple[tuple[object, ...], ...]]:
    snapshot: dict[str, tuple[tuple[object, ...], ...]] = {}
    for model in _PERSISTED_MODELS:
        rows = session.scalars(select(model).order_by(model.id)).all()
        columns = inspect(model).mapper.column_attrs
        snapshot[model.__tablename__] = tuple(
            tuple(getattr(row, column.key) for column in columns) for row in rows
        )
    return snapshot


def test_manual_timestamps_are_aware_utc_after_file_sqlite_reload(
    tmp_path: Path,
) -> None:
    ids = _records()
    command = _command(ids)
    with SessionLocal() as source_session:
        source_draft = source_session.get(EmailDraft, command.email_draft_id)
        assert source_draft is not None
        source_rows = (
            source_session.get(Project, source_draft.project_id),
            source_session.get(Company, source_draft.company_id),
            source_session.get(Contact, source_draft.contact_id),
            source_session.get(Lead, source_draft.lead_id),
            source_session.get(Task, source_draft.task_id),
            source_draft,
        )
        assert all(row is not None for row in source_rows)
        copies = tuple(
            (
                type(row),
                {
                    column.key: getattr(row, column.key)
                    for column in inspect(row).mapper.column_attrs
                },
            )
            for row in source_rows
            if row is not None
        )
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'manual-utc.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sent_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    with factory() as session:
        for model, values in copies:
            session.add(model(**values))
            session.flush()
        record = ManualEmailSendRecord(
            project_id=command.project_id,
            company_id=command.company_id,
            contact_id=command.contact_id,
            email_draft_id=command.email_draft_id,
            recipient_email="recipient@example.test",
            sent_at=sent_at,
        )
        session.add(record)
        session.commit()
        record_id = record.id
    with factory() as fresh_session:
        loaded = fresh_session.get(ManualEmailSendRecord, record_id)
        assert loaded is not None
        assert loaded.sent_at.tzinfo is not None
        assert loaded.sent_at.utcoffset() == datetime.min.replace(tzinfo=UTC).utcoffset()
        assert loaded.sent_at == sent_at
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_at.utcoffset() == datetime.min.replace(tzinfo=UTC).utcoffset()
    engine.dispose()


@pytest.mark.parametrize(
    "status",
    [EmailDraftStatus.DRAFT.value, EmailDraftStatus.REJECTED.value],
)
def test_non_approved_draft_cannot_be_exported(
    status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_runtime_boundaries(monkeypatch)
    ids = _records()
    command = _command(ids)
    with SessionLocal() as session:
        session.execute(
            update(EmailDraft).where(EmailDraft.id == command.email_draft_id).values(status=status)
        )
        session.commit()
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachNotApprovedError):
            service.export(_scope(ids))
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0


def _tamper_subject(ids: dict[str, int]) -> None:
    command = _command(ids)
    with SessionLocal() as session:
        session.execute(
            update(EmailDraft)
            .where(EmailDraft.id == command.email_draft_id)
            .values(subject="Tampered subject")
        )
        session.commit()


def test_tampered_approved_draft_cannot_be_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_runtime_boundaries(monkeypatch)
    ids = _records()
    _tamper_subject(ids)
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachStaleContextError):
            service.export(_scope(ids))


def test_tampered_approved_draft_cannot_be_marked_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_runtime_boundaries(monkeypatch)
    ids = _records()
    _tamper_subject(ids)
    with SessionLocal() as session:
        service = ManualOutreachService(session, ManualEmailSendRecordRepository(session))
        with pytest.raises(ManualOutreachStaleContextError):
            service.mark_sent(_confirmed(ids))
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0


def test_stale_recipient_cannot_be_marked_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_runtime_boundaries(monkeypatch)
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
            service.mark_sent(_confirmed(ids))
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0


def test_real_export_is_zero_runtime_boundary_and_zero_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_runtime_boundaries(monkeypatch)
    _hostile_smtp_environment(monkeypatch)
    ids = _records()
    with SessionLocal() as session:
        before = _state(session)
    result = CliRunner().invoke(
        app,
        ["agent", "email-draft", "export", *_arguments(ids)],
    )
    assert result.exit_code == 0, result.output
    assert "READY_FOR_MANUAL_SEND" in result.stdout
    assert _SECRET not in result.stdout
    assert _SECRET not in result.stderr
    with SessionLocal() as session:
        assert _state(session) == before


def test_real_mark_sent_is_zero_runtime_boundary_without_smtp_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_runtime_boundaries(monkeypatch)
    _hostile_smtp_environment(monkeypatch)
    ids = _records()
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "email-draft",
            "mark-sent",
            *_arguments(ids),
            "--confirm",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["outreach_status"] == ManualOutreachStatus.MANUALLY_SENT.value
    assert _SECRET not in result.stdout
    assert _SECRET not in result.stderr
    with SessionLocal() as session:
        record = session.scalar(select(ManualEmailSendRecord))
        assert record is not None
        assert _SECRET not in repr(record.recipient_email)
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0

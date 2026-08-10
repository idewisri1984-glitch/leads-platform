import socket
import threading
from collections.abc import Callable
from email import policy
from email.parser import BytesParser

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import email_draft as cli
from app.cli.email_draft import execute_send
from app.cli.main import app
from app.core.database import SessionLocal
from app.modules.contact.models import Contact
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryNotApprovedError,
    EmailDeliveryStaleContextError,
)
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.providers.smtp.client import SMTPClient
from app.providers.smtp.contracts import SMTPSecurityMode, SMTPTransportConfig
from app.providers.smtp.fake import FakeSMTPScenario, FakeSMTPTransport

from .test_email_delivery_integration import _SMTPServer
from .test_email_delivery_service import NOW, _records, _sender

runner = CliRunner()


def _command(ids: tuple[int, int, int, int]) -> ConfirmedEmailSendCommand:
    return ConfirmedEmailSendCommand(
        project_id=ids[0],
        company_id=ids[1],
        contact_id=ids[2],
        email_draft_id=ids[3],
        confirmed=True,
    )


def _composition(transport: FakeSMTPTransport) -> Callable:
    return lambda: (transport, _sender())


def _cli_args(ids: tuple[int, int, int, int]) -> list[str]:
    return [
        "agent",
        "email-draft",
        "send",
        "--project-id",
        str(ids[0]),
        "--company-id",
        str(ids[1]),
        "--contact-id",
        str(ids[2]),
        "--email-draft-id",
        str(ids[3]),
        "--confirm",
    ]


def test_executor_uses_one_exact_session_for_repository_and_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    session = SessionLocal()
    observed: dict[str, object] = {}
    real_repository = __import__(
        "app.cli.email_draft", fromlist=["EmailDeliveryAttemptRepository"]
    ).EmailDeliveryAttemptRepository
    real_service = __import__(
        "app.cli.email_draft", fromlist=["ConfirmedEmailSendService"]
    ).ConfirmedEmailSendService

    class RecordingRepository(real_repository):
        def __init__(self, repository_session: Session) -> None:
            observed["repository_session"] = repository_session
            super().__init__(repository_session)

    class RecordingService(real_service):
        def __init__(self, **kwargs: object) -> None:
            observed["service_session"] = kwargs["session"]
            observed["repository"] = kwargs["repository"]
            super().__init__(**kwargs)

    monkeypatch.setattr("app.cli.email_draft.EmailDeliveryAttemptRepository", RecordingRepository)
    monkeypatch.setattr("app.cli.email_draft.ConfirmedEmailSendService", RecordingService)
    rendered = execute_send(
        _command(ids),
        "text",
        session_factory=lambda: session,
        composition_factory=_composition(transport),
    )
    assert 'outcome="ACCEPTED"' in rendered
    assert observed["service_session"] is session
    assert observed["repository_session"] is session
    assert observed["repository"].session is session
    assert len(transport.calls) == 1


def test_executor_happy_path_is_single_send_and_keeps_draft_approved() -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    rendered = execute_send(_command(ids), "json", composition_factory=_composition(transport))
    assert '"outcome":"ACCEPTED"' in rendered
    assert len(transport.calls) == 1
    with SessionLocal() as session:
        attempt = session.scalar(select(EmailDeliveryAttempt))
        draft = session.get(EmailDraft, ids[3])
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.ACCEPTED.value
        assert draft is not None
        assert draft.status == EmailDraftStatus.APPROVED.value


@pytest.mark.parametrize(
    "status",
    [EmailDraftStatus.DRAFT, EmailDraftStatus.REJECTED],
)
def test_executor_blocks_unapproved_draft_without_attempt_or_smtp(status) -> None:
    ids = _records(status)
    transport = FakeSMTPTransport()
    with pytest.raises(EmailDeliveryNotApprovedError):
        execute_send(_command(ids), "text", composition_factory=_composition(transport))
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


def test_executor_blocks_stale_recipient_without_attempt_or_smtp() -> None:
    ids = _records()
    with SessionLocal() as session:
        contact = session.get(Contact, ids[2])
        assert contact is not None
        contact.email = "changed@example.test"
        session.commit()
    transport = FakeSMTPTransport()
    with pytest.raises(EmailDeliveryStaleContextError):
        execute_send(_command(ids), "text", composition_factory=_composition(transport))
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
    assert transport.calls == []


@pytest.mark.parametrize(
    "outcome",
    [
        EmailDeliveryOutcome.RESERVED,
        EmailDeliveryOutcome.ACCEPTED,
        EmailDeliveryOutcome.TRANSIENT_FAILURE,
        EmailDeliveryOutcome.PERMANENT_FAILURE,
        EmailDeliveryOutcome.UNKNOWN,
    ],
)
def test_executor_never_retries_any_existing_attempt(outcome) -> None:
    ids = _records()
    first = FakeSMTPTransport()
    execute_send(_command(ids), "text", composition_factory=_composition(first))
    with SessionLocal() as session:
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        values: dict[str, object] = {
            "outcome": outcome.value,
            "completed_at": NOW,
            "accepted_at": None,
            "unknown_at": None,
            "smtp_classification": None,
            "smtp_code": None,
            "error_category": None,
        }
        if outcome is EmailDeliveryOutcome.RESERVED:
            values["completed_at"] = None
        elif outcome is EmailDeliveryOutcome.ACCEPTED:
            values["accepted_at"] = NOW
            values["smtp_code"] = 250
        elif outcome is EmailDeliveryOutcome.TRANSIENT_FAILURE:
            values["smtp_classification"] = "TRANSIENT"
            values["smtp_code"] = 421
            values["error_category"] = "connection"
        elif outcome is EmailDeliveryOutcome.PERMANENT_FAILURE:
            values["smtp_classification"] = "PERMANENT"
            values["smtp_code"] = 550
            values["error_category"] = "recipient"
        else:
            values["smtp_classification"] = "UNKNOWN"
            values["unknown_at"] = NOW
            values["error_category"] = "unknown"
        session.execute(
            EmailDeliveryAttempt.__table__.update()
            .where(EmailDeliveryAttempt.id == attempt.id)
            .values(**values)
        )
        session.commit()
    second = FakeSMTPTransport()
    with pytest.raises(EmailDeliveryAlreadyAttemptedError):
        execute_send(_command(ids), "text", composition_factory=_composition(second))
    assert len(first.calls) == 1
    assert second.calls == []


def test_cli_tx1_failure_is_safe_and_never_invokes_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    session = SessionLocal()

    def fail_commit() -> None:
        raise SQLAlchemyError("SUPER_SECRET_SMTP_PASSWORD")

    monkeypatch.setattr(session, "commit", fail_commit)

    def execute(command: ConfirmedEmailSendCommand, output: str) -> str:
        return execute_send(
            command,
            output,
            session_factory=lambda: session,
            composition_factory=_composition(transport),
        )

    monkeypatch.setattr(cli, "execute_send", execute)
    result = runner.invoke(app, _cli_args(ids))
    assert result.exit_code == 20
    assert "manual reconciliation" in result.stderr
    assert "ACCEPTED" not in result.output
    assert "SUPER_SECRET_SMTP_PASSWORD" not in result.output
    assert transport.calls == []
    with SessionLocal() as verification_session:
        assert (
            verification_session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0
        )


@pytest.mark.parametrize(
    "scenario",
    [FakeSMTPScenario.SUCCESS, FakeSMTPScenario.CONNECTION_FAILURE],
)
def test_cli_tx2_failure_requires_recovery_and_blocks_resend(
    monkeypatch: pytest.MonkeyPatch,
    scenario: FakeSMTPScenario,
) -> None:
    ids = _records()
    first_transport = FakeSMTPTransport(scenario=scenario)
    failing_session = SessionLocal()
    real_commit = failing_session.commit
    commit_calls = 0

    def fail_second_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise SQLAlchemyError("SUPER_SECRET_SMTP_PASSWORD")
        real_commit()

    monkeypatch.setattr(failing_session, "commit", fail_second_commit)

    def first_execute(command: ConfirmedEmailSendCommand, output: str) -> str:
        return execute_send(
            command,
            output,
            session_factory=lambda: failing_session,
            composition_factory=_composition(first_transport),
        )

    monkeypatch.setattr(cli, "execute_send", first_execute)
    first_result = runner.invoke(app, _cli_args(ids))
    assert first_result.exit_code == 20
    assert "manual reconciliation" in first_result.stderr
    assert "ACCEPTED" not in first_result.output
    assert "SUPER_SECRET_SMTP_PASSWORD" not in first_result.output
    assert len(first_transport.calls) == 1

    second_transport = FakeSMTPTransport()

    def second_execute(command: ConfirmedEmailSendCommand, output: str) -> str:
        return execute_send(
            command,
            output,
            composition_factory=_composition(second_transport),
        )

    monkeypatch.setattr(cli, "execute_send", second_execute)
    second_result = runner.invoke(app, _cli_args(ids))
    assert second_result.exit_code == 15
    assert "retry is not supported" in second_result.stderr
    assert second_transport.calls == []
    with SessionLocal() as verification_session:
        attempt = verification_session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.RESERVED.value


def test_public_cli_uses_production_adapter_once_against_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    server = _SMTPServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    external_dns_calls: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host != "127.0.0.1":
            external_dns_calls.append(host)
            raise AssertionError("external DNS is forbidden")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    transport = SMTPClient(
        SMTPTransportConfig(
            host="127.0.0.1",
            port=int(server.server_address[1]),
            security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
            username=None,
            password=None,
            connection_timeout_seconds=5.0,
        )
    )
    sender = _sender(
        transport_name="stdlib-smtp",
        security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
    )
    monkeypatch.setattr(cli, "_SMTPCompositionFactory", lambda: lambda: (transport, sender))
    try:
        result = runner.invoke(
            app,
            _cli_args(ids),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.exit_code == 0, result.output
    assert 'outcome="ACCEPTED"' in result.stdout
    assert external_dns_calls == []
    assert len(server.messages) == 1
    assert [command.split(" ", 1)[0].upper() for command in server.commands] == [
        "EHLO",
        "MAIL",
        "RCPT",
        "DATA",
        "QUIT",
    ]
    parsed = BytesParser(policy=policy.default).parsebytes(server.messages[0])
    assert parsed["To"] == "recipient@example.test"
    assert parsed["Subject"] == "Reviewed subject"
    assert parsed["Message-ID"] is not None
    assert (
        parsed.get_content().strip() == "Reviewed plain-text body with sufficient stable content."
    )
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1

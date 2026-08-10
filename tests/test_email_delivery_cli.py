import re
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from app.cli import email_draft as cli
from app.cli.main import app
from app.modules.email_delivery.models import EmailDeliveryOutcome
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    ConfirmedEmailSendResult,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryConfigurationError,
    EmailDeliveryPersistenceRecoveryRequiredError,
)

runner = CliRunner()
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _result() -> ConfirmedEmailSendResult:
    return ConfirmedEmailSendResult(
        email_draft_id=4,
        delivery_attempt_id=9,
        recipient="recipient@example.test",
        message_id="<ed-token@mail.example.test>",
        outcome=EmailDeliveryOutcome.ACCEPTED,
        created_at=NOW,
        completed_at=NOW,
        accepted_at=NOW,
        smtp_classification=None,
        smtp_code=250,
        error_category=None,
    )


def _args(*extra: str) -> list[str]:
    return [
        "agent",
        "email-draft",
        "send",
        "--project-id",
        "1",
        "--company-id",
        "2",
        "--contact-id",
        "3",
        "--email-draft-id",
        "4",
        *extra,
    ]


def test_send_requires_explicit_confirmation_before_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("executor must not run")

    monkeypatch.setattr(cli, "execute_send", forbidden)
    result = runner.invoke(app, _args())
    assert result.exit_code == 3
    assert calls == 0
    assert "requires --confirm" in result.stderr


def test_send_rejects_duplicate_email_draft_id_before_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("executor must not run")

    monkeypatch.setattr(cli, "execute_send", forbidden)
    arguments = _args("--confirm")
    arguments[arguments.index("--email-draft-id") + 2 : arguments.index("--email-draft-id") + 2] = [
        "--email-draft-id",
        "5",
    ]
    result = runner.invoke(app, arguments)
    assert result.exit_code == 2
    assert calls == 0
    assert "invalid" in result.stderr.casefold()


def test_send_help_is_available_without_confirmation() -> None:
    result = runner.invoke(app, ["agent", "email-draft", "send", "--help"])
    assert result.exit_code == 0
    assert "--confirm" in ANSI_ESCAPE.sub("", result.stdout)


def test_send_constructs_exact_confirmed_command_and_safe_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ConfirmedEmailSendCommand] = []

    def execute(command: ConfirmedEmailSendCommand, output: str) -> str:
        captured.append(command)
        return cli.render_email_delivery(_result(), output)

    monkeypatch.setattr(cli, "execute_send", execute)
    result = runner.invoke(app, _args("--confirm"))
    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0] == ConfirmedEmailSendCommand(
        project_id=1,
        company_id=2,
        contact_id=3,
        email_draft_id=4,
        confirmed=True,
    )
    assert 'outcome="ACCEPTED"' in result.stdout
    assert "delivered" not in result.stdout.casefold()
    assert "body" not in result.stdout.casefold()


@pytest.mark.parametrize("output", ["text", "json"])
def test_delivery_rendering_contains_only_safe_audit_fields(output: str) -> None:
    rendered = cli.render_email_delivery(_result(), output)
    assert "recipient@example.test" in rendered
    assert "<ed-token@mail.example.test>" in rendered
    assert "ACCEPTED" in rendered
    assert "SUPER_SECRET_SMTP_PASSWORD" not in rendered


def test_existing_unknown_attempt_reports_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(*args: object, **kwargs: object) -> str:
        raise EmailDeliveryAlreadyAttemptedError(9, "UNKNOWN")

    monkeypatch.setattr(cli, "execute_send", execute)
    result = runner.invoke(app, _args("--confirm"))
    assert result.exit_code == 15
    assert "outcome=UNKNOWN" in result.stderr
    assert "retry is not supported" in result.stderr
    assert "SUPER_SECRET_SMTP_PASSWORD" not in result.stderr


def test_persistence_recovery_never_prints_success_or_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def execute(*args: object, **kwargs: object) -> str:
        raise EmailDeliveryPersistenceRecoveryRequiredError("SUPER_SECRET_SMTP_PASSWORD")

    monkeypatch.setattr(cli, "execute_send", execute)
    result = runner.invoke(app, _args("--confirm"))
    assert result.exit_code == 20
    assert "manual reconciliation" in result.stderr
    assert "retry is not supported" in result.stderr
    assert "ACCEPTED" not in result.stdout
    assert "SUPER_SECRET_SMTP_PASSWORD" not in result.output


def test_invalid_smtp_configuration_fails_before_session_creation() -> None:
    session_calls = 0

    def session_factory():
        nonlocal session_calls
        session_calls += 1
        raise AssertionError("session must not be created")

    def composition_factory():
        raise EmailDeliveryConfigurationError("Email delivery configuration is invalid.")

    with pytest.raises(EmailDeliveryConfigurationError):
        cli.execute_send(
            ConfirmedEmailSendCommand(
                project_id=1,
                company_id=2,
                contact_id=3,
                email_draft_id=4,
                confirmed=True,
            ),
            "text",
            session_factory=session_factory,
            composition_factory=composition_factory,
        )
    assert session_calls == 0

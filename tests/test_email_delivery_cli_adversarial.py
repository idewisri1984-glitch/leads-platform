import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from app.cli import email_draft as cli
from app.cli.main import app
from app.core.database import SessionLocal
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.providers.smtp.fake import FakeSMTPTransport

from .test_email_delivery_cli_integration import _cli_args, _composition
from .test_email_delivery_service import _records

runner = CliRunner()
_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "arguments",
    [
        ["agent", "email-draft", "send"],
        ["agent", "email-draft", "send", "--confirm", "--unknown"],
        [
            "agent",
            "email-draft",
            "send",
            "--project-id",
            "01",
            "--company-id",
            "2",
            "--contact-id",
            "3",
            "--email-draft-id",
            "4",
            "--confirm",
        ],
        [
            "agent",
            "email-draft",
            "send",
            "--project-id",
            "1",
            "--project-id",
            "2",
            "--company-id",
            "2",
            "--contact-id",
            "3",
            "--email-draft-id",
            "4",
            "--confirm",
        ],
        [
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
            "--confirm",
            "--output",
            "yaml",
        ],
    ],
    ids=["missing-confirm", "unknown-option", "noncanonical-id", "duplicate-id", "bad-output"],
)
def test_parser_adversarial_matrix_never_reaches_send(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    calls = 0

    def forbidden(*_args: object, **_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("send boundary reached")

    monkeypatch.setattr(cli, "execute_send", forbidden)
    result = runner.invoke(app, arguments)
    assert result.exit_code != 0
    assert calls == 0
    assert "ACCEPTED" not in result.output


def test_output_broken_pipe_does_not_repeat_service_or_erase_accepted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _records()
    transport = FakeSMTPTransport()
    real_execute = cli.execute_send
    service_calls = 0

    def execute(command, output):
        nonlocal service_calls
        service_calls += 1
        return real_execute(command, output, composition_factory=_composition(transport))

    def broken_output(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError("output closed")

    monkeypatch.setattr(cli, "execute_send", execute)
    monkeypatch.setattr(cli.typer, "echo", broken_output)
    result = runner.invoke(app, _cli_args(ids))
    assert result.exit_code != 0
    assert service_calls == 1
    assert len(transport.calls) == 1
    with SessionLocal() as session:
        attempt = session.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.ACCEPTED.value


def test_unicode_body_and_secret_never_appear_in_success_output() -> None:
    ids = _records()
    with SessionLocal() as session:
        from app.modules.email_draft.context import build_content_hash
        from app.modules.email_draft.models import EmailDraft

        draft = session.get(EmailDraft, ids[3])
        assert draft is not None
        text_body = "Reviewed Unicode body: \u4f60\u597d. SUPER_SECRET_SMTP_PASSWORD"
        content_hash = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=text_body,
            prompt_version=draft.prompt_version,
        )
        session.execute(
            EmailDraft.__table__.update()
            .where(EmailDraft.id == draft.id)
            .values(text_body=text_body, content_hash=content_hash)
        )
        session.commit()
    transport = FakeSMTPTransport()
    original = cli.execute_send

    def execute(command, output):
        return original(
            command,
            output,
            composition_factory=_composition(transport),
        )

    try:
        cli.execute_send = execute
        result = runner.invoke(app, [*_cli_args(ids), "--output", "json"])
    finally:
        cli.execute_send = original
    assert result.exit_code == 0, result.output
    assert "SUPER_SECRET_SMTP_PASSWORD" not in result.output
    assert "Reviewed Unicode body" not in result.output
    assert result.stdout.startswith("{")
    assert result.stdout.rstrip().endswith("}")


def test_non_send_help_paths_survive_hostile_smtp_environment() -> None:
    proof = r"""
import smtplib
import socket
from typer.testing import CliRunner

def forbidden(*args, **kwargs):
    raise AssertionError("runtime boundary")

smtplib.SMTP = forbidden
smtplib.SMTP_SSL = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
from app.cli.main import app
commands = [
    ["agent", "email-draft", "generate", "--help"],
    ["agent", "email-draft", "show", "--help"],
    ["agent", "email-draft", "approve", "--help"],
    ["agent", "email-draft", "reject", "--help"],
]
print([CliRunner().invoke(app, command).exit_code for command in commands])
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_ROOT)
    environment["DEBUG"] = "false"
    environment.update(
        {
            "SMTP_HOST": " bad host ",
            "SMTP_PORT": "invalid",
            "SMTP_SECURITY_MODE": "INVALID",
            "SMTP_PASSWORD": "SUPER_SECRET_SMTP_PASSWORD",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", proof],
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[0, 0, 0, 0]"
    assert "SUPER_SECRET_SMTP_PASSWORD" not in result.stdout + result.stderr


def test_hostile_dotenv_cannot_override_explicit_invalid_smtp_environment(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "SMTP_HOST=real.example.test\nSMTP_PASSWORD=SUPER_SECRET_SMTP_PASSWORD\n",
        encoding="utf-8",
    )
    proof = r"""
import smtplib
import socket
counts = {"network": 0}
def forbidden(*args, **kwargs):
    counts["network"] += 1
    raise AssertionError("network")
smtplib.SMTP = forbidden
smtplib.SMTP_SSL = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
from app.cli.email_draft import _SMTPCompositionFactory
try:
    _SMTPCompositionFactory()()
except Exception as error:
    print(type(error).__name__)
print(counts)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_ROOT)
    environment["DEBUG"] = "false"
    environment["SMTP_HOST"] = " bad explicit host "
    environment["SMTP_PORT"] = "invalid"
    result = subprocess.run(
        [sys.executable, "-c", proof],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "EmailDeliveryConfigurationError",
        "{'network': 0}",
    ]
    assert "SUPER_SECRET_SMTP_PASSWORD" not in result.stdout + result.stderr

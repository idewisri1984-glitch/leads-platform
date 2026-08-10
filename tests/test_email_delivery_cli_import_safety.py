import os
import subprocess
import sys
import tempfile

import pytest

_SMTP_ENVIRONMENT = {
    "SMTP_HOST": "smtp.example.test",
    "SMTP_PORT": "587",
    "SMTP_SECURITY_MODE": "STARTTLS",
    "SMTP_USERNAME": "smtp-user",
    "SMTP_PASSWORD": "SUPER_SECRET_SMTP_PASSWORD",
    "SMTP_TIMEOUT_SECONDS": "5.0",
    "SMTP_ENVELOPE_FROM": "bounce@example.test",
    "SMTP_HEADER_FROM_EMAIL": "sender@example.test",
    "SMTP_HEADER_FROM_NAME": "Sender Name",
    "SMTP_REPLY_TO": "reply@example.test",
    "SMTP_MESSAGE_ID_DOMAIN": "mail.example.test",
    "SMTP_TRANSPORT_NAME": "stdlib-smtp",
}


def _run(
    proof: str,
    smtp_environment: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    environment.pop("OPENAI_API_KEY", None)
    environment["PYTHONPATH"] = os.getcwd()
    if smtp_environment is not None:
        for name in _SMTP_ENVIRONMENT:
            environment.pop(name, None)
        for name, value in {**_SMTP_ENVIRONMENT, **smtp_environment}.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
    with tempfile.TemporaryDirectory(prefix="leads-stage5c3-env-") as directory:
        return subprocess.run(
            [sys.executable, "-c", proof],
            cwd=directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )


_INVALID_COMPOSITION_PROOF = r"""
import smtplib
import socket

counts = {"session": 0, "smtp": 0, "socket": 0, "dns": 0}

def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail

smtplib.SMTP = blocked("smtp")
smtplib.SMTP_SSL = blocked("smtp")
socket.create_connection = blocked("socket")
socket.getaddrinfo = blocked("dns")

from app.cli.email_draft import execute_send
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    EmailDeliveryConfigurationError,
)

def session_factory():
    counts["session"] += 1
    raise AssertionError("session")

try:
    execute_send(
        ConfirmedEmailSendCommand(
            project_id=1,
            company_id=2,
            contact_id=3,
            email_draft_id=4,
            confirmed=True,
        ),
        "text",
        session_factory=session_factory,
    )
except Exception as error:
    print(type(error).__name__)
    print(counts)
    print("SUPER_SECRET_SMTP_PASSWORD" in str(error) or "SUPER_SECRET_SMTP_PASSWORD" in repr(error))
else:
    print("NO_ERROR")
"""


@pytest.mark.parametrize(
    "changes",
    [
        {"SMTP_HOST": None},
        {"SMTP_PORT": "not-an-integer"},
        {"SMTP_PORT": "0"},
        {"SMTP_PORT": "65536"},
        {"SMTP_SECURITY_MODE": "INVALID"},
        {"SMTP_TIMEOUT_SECONDS": "0"},
        {"SMTP_ENVELOPE_FROM": "invalid"},
        {"SMTP_HEADER_FROM_EMAIL": "invalid"},
        {"SMTP_REPLY_TO": "invalid"},
        {"SMTP_MESSAGE_ID_DOMAIN": "invalid"},
        {"SMTP_USERNAME": "smtp-user", "SMTP_PASSWORD": None},
        {"SMTP_USERNAME": None, "SMTP_PASSWORD": "SUPER_SECRET_SMTP_PASSWORD"},
    ],
    ids=[
        "missing-host",
        "non-integer-port",
        "zero-port",
        "out-of-range-port",
        "invalid-security",
        "invalid-timeout",
        "invalid-envelope-from",
        "invalid-header-from",
        "invalid-reply-to",
        "invalid-message-id-domain",
        "username-without-password",
        "password-without-username",
    ],
)
def test_production_smtp_composition_rejects_invalid_environment_before_boundaries(
    changes: dict[str, str | None],
) -> None:
    result = _run(_INVALID_COMPOSITION_PROOF, changes)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "EmailDeliveryConfigurationError",
        "{'session': 0, 'smtp': 0, 'socket': 0, 'dns': 0}",
        "False",
    ]


@pytest.mark.parametrize(
    ("mode", "host", "username", "password"),
    [
        ("STARTTLS", "smtp.example.test", "smtp-user", "SUPER_SECRET_SMTP_PASSWORD"),
        ("TLS_IMPLICIT", "smtp.example.test", "smtp-user", "SUPER_SECRET_SMTP_PASSWORD"),
        ("PLAINTEXT_LOCAL_TEST_ONLY", "127.0.0.1", None, None),
    ],
)
def test_production_smtp_composition_builds_expected_transport_without_network(
    mode: str,
    host: str,
    username: str | None,
    password: str | None,
) -> None:
    proof = r"""
import smtplib
import socket

def forbidden(*args, **kwargs):
    raise AssertionError("network")

smtplib.SMTP = forbidden
smtplib.SMTP_SSL = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden

from app.cli.email_draft import _SMTPCompositionFactory
from app.providers.smtp.client import SMTPClient

transport, sender = _SMTPCompositionFactory()()
assert type(transport) is SMTPClient
print(transport.config.host)
print(transport.config.port)
print(transport.config.security_mode.value)
print(transport.config.connection_timeout_seconds)
print(transport.config.username)
print(sender.envelope_from)
print(sender.header_from_email)
print(sender.header_from_name)
print(sender.reply_to)
print(sender.message_id_domain)
print(sender.transport_name)
rendered = repr(transport.config)
print("SUPER_SECRET_SMTP_PASSWORD" in rendered)
"""
    result = _run(
        proof,
        {
            "SMTP_SECURITY_MODE": mode,
            "SMTP_HOST": host,
            "SMTP_USERNAME": username,
            "SMTP_PASSWORD": password,
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        host,
        "587",
        mode,
        "5.0",
        str(username),
        "bounce@example.test",
        "sender@example.test",
        "Sender Name",
        "reply@example.test",
        "mail.example.test",
        "stdlib-smtp",
        "False",
    ]


def test_email_draft_cli_import_does_not_construct_runtime_dependencies() -> None:
    proof = r"""
import smtplib
import socket
import sys
import pydantic_settings
import sqlalchemy
import sqlalchemy.orm

counts = {
    name: 0
    for name in (
        "settings", "engine", "sessionmaker", "session", "smtp", "socket", "dns"
    )
}

def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail

pydantic_settings.BaseSettings.__init__ = blocked("settings")
sqlalchemy.create_engine = blocked("engine")
sqlalchemy.orm.sessionmaker = blocked("sessionmaker")
sqlalchemy.orm.Session.__init__ = blocked("session")
smtplib.SMTP = blocked("smtp")
smtplib.SMTP_SSL = blocked("smtp")
socket.create_connection = blocked("socket")
socket.getaddrinfo = blocked("dns")

import app.cli.email_draft

forbidden = [
    "app.core.config.settings",
    "app.core.database.engine",
    "app.core.database.session",
    "app.providers.smtp.client",
    "app.providers.openai_email.client",
    "openai",
]
print(counts)
print(",".join(name for name in forbidden if name in sys.modules))
"""
    result = _run(proof)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "{'settings': 0, 'engine': 0, 'sessionmaker': 0, 'session': 0, "
        "'smtp': 0, 'socket': 0, 'dns': 0}",
        "",
    ]


def test_send_help_does_not_construct_runtime_dependencies() -> None:
    proof = r"""
import smtplib
import socket
import pydantic_settings
import sqlalchemy
import sqlalchemy.orm
from typer.testing import CliRunner

counts = {
    name: 0
    for name in (
        "settings", "engine", "sessionmaker", "session", "smtp", "socket", "dns"
    )
}

def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail

pydantic_settings.BaseSettings.__init__ = blocked("settings")
sqlalchemy.create_engine = blocked("engine")
sqlalchemy.orm.sessionmaker = blocked("sessionmaker")
sqlalchemy.orm.Session.__init__ = blocked("session")
smtplib.SMTP = blocked("smtp")
smtplib.SMTP_SSL = blocked("smtp")
socket.create_connection = blocked("socket")
socket.getaddrinfo = blocked("dns")

from app.cli.main import app
result = CliRunner().invoke(app, ["agent", "email-draft", "send", "--help"])
print(result.exit_code)
print(counts)
"""
    result = _run(proof)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "0",
        "{'settings': 0, 'engine': 0, 'sessionmaker': 0, 'session': 0, "
        "'smtp': 0, 'socket': 0, 'dns': 0}",
    ]

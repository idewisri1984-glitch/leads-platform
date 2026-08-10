import os
import subprocess
import sys


def _run(proof: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-c", proof],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


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

import os
import subprocess
import sys


def test_email_delivery_imports_do_not_open_runtime_boundaries() -> None:
    proof = r"""
import smtplib
import socket
import sys

counts = {"smtp": 0, "socket": 0, "dns": 0}

def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail

smtplib.SMTP = blocked("smtp")
smtplib.SMTP_SSL = blocked("smtp")
socket.create_connection = blocked("socket")
socket.getaddrinfo = blocked("dns")

import app.modules.email_delivery
import app.modules.email_delivery.models
import app.modules.email_delivery.schemas
import app.modules.email_delivery.repository

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
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", proof],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "{'smtp': 0, 'socket': 0, 'dns': 0}",
        "",
    ]


def test_email_delivery_service_import_has_no_runtime_side_effects() -> None:
    proof = r"""
import smtplib
import socket
import sys

import pydantic_settings
import sqlalchemy
import sqlalchemy.orm

counts = {
    "settings": 0,
    "engine": 0,
    "sessionmaker": 0,
    "session": 0,
    "smtp": 0,
    "socket": 0,
    "dns": 0,
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

import app
import app.modules
import app.modules.email_delivery
import app.modules.email_delivery.service

forbidden = [
    "app.core.config.settings",
    "app.core.database.engine",
    "app.core.database.session",
    "app.providers.smtp.client",
    "app.providers.openai_email.client",
    "app.modules.email_draft.fake_provider",
    "openai",
]
print(counts)
print(",".join(name for name in forbidden if name in sys.modules))
"""
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", proof],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "{'settings': 0, 'engine': 0, 'sessionmaker': 0, 'session': 0, "
        "'smtp': 0, 'socket': 0, 'dns': 0}",
        "",
    ]

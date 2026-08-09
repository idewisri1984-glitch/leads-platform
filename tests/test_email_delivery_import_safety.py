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

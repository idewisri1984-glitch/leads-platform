import os
import subprocess
import sys
from pathlib import Path

from app.modules.email_draft.models import EmailDraftStatus

_ROOT = Path(__file__).resolve().parents[1]


def test_smtp_provider_import_and_existing_cli_paths_open_no_boundaries() -> None:
    proof = """
import smtplib
import socket
from typer.testing import CliRunner
counts = {'smtp': 0, 'socket': 0, 'dns': 0}
def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail
smtplib.SMTP = blocked('smtp')
smtplib.SMTP_SSL = blocked('smtp')
socket.create_connection = blocked('socket')
socket.getaddrinfo = blocked('dns')
import app.cli.agent
import app.cli.email_draft
import app.providers.smtp
from app.cli.main import app
runner = CliRunner()
commands = [
    ['--help'],
    ['agent', 'email-draft', '--help'],
    ['agent', 'email-draft', 'approve', '--help'],
    ['agent', 'email-draft', 'approve', '--project-id', '1', '--company-id', '2',
     '--contact-id', '3', '--draft-id', '4'],
]
results = [runner.invoke(app, command) for command in commands]
print(','.join(str(result.exit_code) for result in results))
print(counts)
"""
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", proof],
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "0,0,0,3",
        "{'smtp': 0, 'socket': 0, 'dns': 0}",
    ]


def test_no_business_workflow_imports_smtp_transport_or_exposes_send_cli() -> None:
    business_paths = [
        _ROOT / "app" / "cli" / "agent.py",
        _ROOT / "app" / "cli" / "email_draft.py",
        _ROOT / "app" / "modules" / "email_draft" / "service.py",
        _ROOT / "app" / "modules" / "agent" / "contact_apply.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in business_paths)
    assert "app.providers.smtp" not in combined
    assert "smtplib" not in combined
    assert "email-draft send" not in combined


def test_email_draft_lifecycle_has_no_delivery_statuses() -> None:
    assert {status.value for status in EmailDraftStatus} == {"DRAFT", "APPROVED", "REJECTED"}

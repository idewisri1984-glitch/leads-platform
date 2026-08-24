import os
import subprocess
import sys


def run(code: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_import_and_help_do_not_load_settings_engine_openai_or_smtp() -> None:
    proof = """
import sys
from typer.testing import CliRunner
import app.cli.agent
import app.cli.email_draft
from app.cli.main import app
runner = CliRunner()
commands = [
    ['--help'],
    ['agent', 'email-draft', '--help'],
    ['agent', 'email-draft', 'generate', '--help'],
    ['agent', 'email-draft', 'show', '--help'],
    ['agent', 'email-draft', 'approve', '--help'],
    ['agent', 'email-draft', 'reject', '--help'],
    ['agent', 'email-draft', 'rebind-person-recipient', '--help'],
    ['agent', 'email-draft', 'show', '--unknown'],
    [
        'agent', 'email-draft', 'approve', '--project-id', '1', '--company-id', '2',
        '--contact-id', '3', '--draft-id', '4'
    ],
]
results = [runner.invoke(app, command) for command in commands]
from app.modules.email_draft import (
    PersonRecipientRebindingInput, PersonRecipientRebindingResult,
    PersonRecipientRebindingService,
)
assert all(
    (
        PersonRecipientRebindingInput,
        PersonRecipientRebindingResult,
        PersonRecipientRebindingService,
    )
)
forbidden = [
    'app.core.config.settings', 'app.core.database.engine', 'app.core.database.session',
    'app.providers.openai_email.client', 'openai', 'smtplib'
]
loaded = [name for name in forbidden if name in sys.modules]
print(','.join(str(result.exit_code) for result in results))
print(','.join(loaded))
"""
    result = run(proof)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["0,0,0,0,0,0,0,2,3", ""]

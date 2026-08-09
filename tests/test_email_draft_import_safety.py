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
from app.cli.main import app
result = CliRunner().invoke(app, ['agent', 'email-draft', 'approve', '--help'])
forbidden = [
    'app.core.config.settings', 'app.core.database.engine', 'app.core.database.session',
    'app.providers.openai_email.client', 'openai', 'smtplib'
]
loaded = [name for name in forbidden if name in sys.modules]
print(result.exit_code)
print(','.join(loaded))
"""
    result = run(proof)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["0", ""]

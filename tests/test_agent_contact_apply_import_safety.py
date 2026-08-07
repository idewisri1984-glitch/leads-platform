import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("action", ["import", "help", "missing-yes", "malformed"])
def test_contact_apply_fresh_process_excludes_plan_dependencies(action: str) -> None:
    script = r"""
import importlib.abc
import sys

from typer.testing import CliRunner

import app.core.database.session
import app.modules.agent

FORBIDDEN = (
    "app.core.config.settings",
    "app.modules.company_discovery.serpapi_provider",
    "app.modules.contact_discovery.website_provider",
    "app.providers.openai_decision",
    "app.providers.serpapi.client",
)

for loaded in tuple(sys.modules):
    if any(loaded == name or loaded.startswith(f"{name}.") for name in FORBIDDEN):
        del sys.modules[loaded]


class ForbiddenImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(f"{name}.") for name in FORBIDDEN):
            raise AssertionError(f"forbidden import: {fullname}")
        return None


sys.meta_path.insert(0, ForbiddenImport())
import app.cli.agent as agent_cli

if any(name in sys.modules for name in FORBIDDEN):
    raise AssertionError("a forbidden module was imported")


def forbidden_session():
    raise AssertionError("Session was created")


agent_cli.SessionLocal = forbidden_session
action = sys.argv[1]
if action == "import":
    raise SystemExit(0)

runner = CliRunner()
valid = [
    "--project-id", "1",
    "--company-id", "2",
    "--candidate-id", "3",
    "--goal", "Partner",
    "--handoff-token", "a" * 64,
]
if action == "help":
    arguments = ["contact-select", "apply", "--help"]
    expected = 0
elif action == "missing-yes":
    arguments = ["contact-select", "apply", *valid]
    expected = 3
else:
    arguments = ["contact-select", "apply", *valid, "--yes", "--unknown"]
    expected = 2
result = runner.invoke(agent_cli.app, arguments)
if result.exit_code != expected:
    raise AssertionError((result.exit_code, result.stdout, result.stderr, result.exception))
if any(name in sys.modules for name in FORBIDDEN):
    raise AssertionError("a forbidden module was imported during Contact Apply")
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script, action],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

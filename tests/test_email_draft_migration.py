import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REVISION = "9d6e7f8091a2"


def alembic(database: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    environment["DEBUG"] = "false"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def test_email_draft_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "email-draft.sqlite3"
    alembic(database, "upgrade", "8c5d6e7f8091")
    alembic(database, "upgrade", _REVISION)
    assert revision(database) == _REVISION
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info('email_drafts')")}
        assert {
            "recipient_email",
            "subject",
            "text_body",
            "prompt_version",
            "context_fingerprint",
            "content_hash",
            "status",
        } <= columns
        foreign_tables = {
            row[2] for row in connection.execute("PRAGMA foreign_key_list('email_drafts')")
        }
        assert foreign_tables == {"projects", "companies", "contacts", "leads", "tasks"}
    alembic(database, "downgrade", "8c5d6e7f8091")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='email_drafts'"
            ).fetchone()
            is None
        )
    alembic(database, "upgrade", "head")
    assert revision(database) == _REVISION
    alembic(database, "check")

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

_ROOT = Path(__file__).resolve().parents[1]
_PREVIOUS_REVISION = "a41bc92d7e60"
_REVISION = "b52cd03e8f71"


def _alembic(database: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
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


def _revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def test_manual_outreach_migration_upgrade_downgrade_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "manual-outreach.sqlite3"
    _alembic(database, "upgrade", _PREVIOUS_REVISION)
    _alembic(database, "upgrade", _REVISION)
    assert _revision(database) == _REVISION
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    inspector = inspect(engine)
    assert "manual_email_send_records" in inspector.get_table_names()
    assert "delivery_mode" in {column["name"] for column in inspector.get_columns("email_drafts")}
    assert {
        item["name"] for item in inspector.get_unique_constraints("manual_email_send_records")
    } == {"uq_manual_email_send_records_email_draft_id"}
    assert "ck_email_drafts_delivery_mode" in {
        item["name"] for item in inspector.get_check_constraints("email_drafts")
    }
    engine.dispose()
    _alembic(database, "downgrade", _PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='manual_email_send_records'"
            ).fetchone()
            is None
        )
        assert "delivery_mode" not in {
            row[1] for row in connection.execute("PRAGMA table_info('email_drafts')")
        }
    _alembic(database, "upgrade", "head")
    assert _revision(database) == _REVISION
    _alembic(database, "check")

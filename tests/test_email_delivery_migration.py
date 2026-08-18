import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

_ROOT = Path(__file__).resolve().parents[1]
_PREVIOUS_REVISION = "9d6e7f8091a2"
_REVISION = "93dfda21cf4f"
_HEAD = "e41f7a9c2b60"


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


def test_email_delivery_attempt_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "email-delivery-migration.sqlite3"
    _alembic(database, "upgrade", _PREVIOUS_REVISION)
    _alembic(database, "upgrade", _REVISION)
    assert _revision(database) == _REVISION
    with sqlite3.connect(database) as connection:
        original_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('email_delivery_attempts')")
        }
    assert "row_version" not in original_columns

    _alembic(database, "upgrade", _HEAD)
    assert _revision(database) == _HEAD

    engine = create_engine(f"sqlite+pysqlite:///{database}")
    inspector = inspect(engine)
    assert "email_delivery_attempts" in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns("email_delivery_attempts")} == {
        "id",
        "email_draft_id",
        "attempt_key",
        "outcome",
        "recipient_email",
        "envelope_from",
        "header_from_email",
        "header_from_name",
        "reply_to",
        "message_id",
        "content_hash",
        "transport_name",
        "security_mode",
        "smtp_classification",
        "smtp_code",
        "error_category",
        "created_at",
        "completed_at",
        "accepted_at",
        "unknown_at",
        "updated_at",
        "row_version",
    }
    row_version = next(
        column
        for column in inspector.get_columns("email_delivery_attempts")
        if column["name"] == "row_version"
    )
    assert row_version["nullable"] is False
    assert str(row_version["default"]).strip("'()") == "1"
    unique_names = {
        item["name"] for item in inspector.get_unique_constraints("email_delivery_attempts")
    }
    assert unique_names == {
        "uq_email_delivery_attempts_email_draft_id",
        "uq_email_delivery_attempts_attempt_key",
        "uq_email_delivery_attempts_message_id",
    }
    check_names = {
        item["name"] for item in inspector.get_check_constraints("email_delivery_attempts")
    }
    assert check_names == {
        "ck_email_delivery_attempts_classification",
        "ck_email_delivery_attempts_nonblank_identity",
        "ck_email_delivery_attempts_outcome",
        "ck_email_delivery_attempts_outcome_fields",
        "ck_email_delivery_attempts_smtp_code",
    }
    assert {item["name"] for item in inspector.get_indexes("email_delivery_attempts")} == {
        "ix_email_delivery_attempts_created_at",
        "ix_email_delivery_attempts_outcome",
    }
    foreign_key = inspector.get_foreign_keys("email_delivery_attempts")
    assert len(foreign_key) == 1
    assert foreign_key[0]["name"] == "fk_email_delivery_attempts_email_draft_id"
    assert foreign_key[0]["referred_table"] == "email_drafts"
    assert foreign_key[0]["options"] == {"ondelete": "RESTRICT"}
    engine.dispose()

    _alembic(database, "downgrade", _REVISION)
    with sqlite3.connect(database) as connection:
        downgraded_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('email_delivery_attempts')")
        }
    assert "row_version" not in downgraded_columns
    assert "outcome" in downgraded_columns

    _alembic(database, "upgrade", _HEAD)
    assert _revision(database) == _HEAD
    _alembic(database, "check")

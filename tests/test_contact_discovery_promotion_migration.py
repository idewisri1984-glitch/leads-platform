import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_TABLE = "contact_discovery_candidates"
_REVISION = "8c5d6e7f8091"
_HEAD = "d82f4c6a91b3"


def alembic(database: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    environment["DEBUG"] = "false"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_REPOSITORY_ROOT,
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


def candidate_columns(database: Path) -> dict[str, tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return {
            str(row[1]): row
            for row in connection.execute(f"PRAGMA table_info('{_TABLE}')").fetchall()
        }


def test_contact_candidate_promotion_link_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "contact-promotion.sqlite"
    alembic(database, "upgrade", "7b4c5d6e7f80")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        project_id = connection.execute(
            "INSERT INTO projects (name) VALUES ('Promotion')"
        ).lastrowid
        company_id = connection.execute(
            "INSERT INTO companies (project_id, name, status) VALUES (?, 'Company', 'NEW')",
            (project_id,),
        ).lastrowid
        contact_id = connection.execute(
            "INSERT INTO contacts (company_id, first_name, status) VALUES (?, 'Ada', 'NEW')",
            (company_id,),
        ).lastrowid
        candidate_id = connection.execute(
            f"INSERT INTO {_TABLE} "
            "(company_id, name, title, email, normalized_email, phone, source_url, "
            "source_type, confidence, discovery_status, deduplication_key, notes, "
            "last_error, created_at, updated_at) VALUES "
            "(?, 'Ada', 'Director', 'ada@example.com', 'ada@example.com', '+15550100', "
            "'https://example.com/team', 'TEAM_PAGE', 80, 'REVIEWED', "
            "'email:ada@example.com', 'keep', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (company_id,),
        ).lastrowid
        connection.commit()

    alembic(database, "upgrade", _REVISION)
    assert revision(database) == _REVISION
    promoted_column = candidate_columns(database)["promoted_contact_id"]
    assert promoted_column[3] == 0
    assert promoted_column[4] is None
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        foreign_keys = connection.execute(f"PRAGMA foreign_key_list('{_TABLE}')").fetchall()
        assert any(
            row[2] == "contacts"
            and row[3] == "promoted_contact_id"
            and row[4] == "id"
            and row[6] == "SET NULL"
            for row in foreign_keys
        )
        indexes = connection.execute(f"PRAGMA index_list('{_TABLE}')").fetchall()
        assert any(
            row[1] == "ix_contact_discovery_candidates_promoted_contact_id" and row[2] == 0
            for row in indexes
        )
        row = connection.execute(
            f"SELECT promoted_contact_id, discovery_status, notes FROM {_TABLE} WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        assert row == (None, "REVIEWED", "keep")
        connection.execute(
            f"UPDATE {_TABLE} SET promoted_contact_id = ? WHERE id = ?",
            (contact_id, candidate_id),
        )
        connection.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        assert connection.execute(
            f"SELECT promoted_contact_id FROM {_TABLE} WHERE id = ?", (candidate_id,)
        ).fetchone() == (None,)
        surviving_contact_id = connection.execute(
            "INSERT INTO contacts (company_id, first_name, status) VALUES (?, 'Grace', 'NEW')",
            (company_id,),
        ).lastrowid
        connection.commit()

    alembic(database, "downgrade", "7b4c5d6e7f80")
    assert "promoted_contact_id" not in candidate_columns(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            f"SELECT discovery_status, notes FROM {_TABLE} WHERE id = ?", (candidate_id,)
        ).fetchone() == ("REVIEWED", "keep")
        assert connection.execute(
            "SELECT first_name FROM contacts WHERE id = ?", (surviving_contact_id,)
        ).fetchone() == ("Grace",)

    alembic(database, "upgrade", "head")
    assert revision(database) == _HEAD
    assert candidate_columns(database)["promoted_contact_id"][3] == 0
    alembic(database, "check")

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REVISION = "8c5d6e7f8091"
_PREVIOUS_REVISION = "7b4c5d6e7f80"
_DOWNGRADE_ERROR = "F7H0 downgrade refused: contacts.first_name contains NULL values."
_UPGRADE_ERROR = (
    "F7H0 upgrade refused: contacts contains a row without a meaningful name or channel."
)


def alembic(
    database: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    environment["DEBUG"] = "false"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=check,
        close_fds=True,
        capture_output=True,
        text=True,
    )


def current_revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def columns(database: Path, table: str) -> dict[str, tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return {
            str(row[1]): row
            for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()
        }


def table_sql(database: Path, table: str) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def create_company(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        project_id = connection.execute(
            "INSERT INTO projects (name) VALUES ('F7H0 migration')"
        ).lastrowid
        company_id = connection.execute(
            "INSERT INTO companies (project_id, name, status) VALUES (?, 'Company', 'NEW')",
            (project_id,),
        ).lastrowid
    assert company_id is not None
    return int(company_id)


def test_upgrade_preserves_named_contact_and_supports_generic_channels(tmp_path: Path) -> None:
    database = tmp_path / "f7h0-upgrade.sqlite"
    alembic(database, "upgrade", _PREVIOUS_REVISION)
    company_id = create_company(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO contacts (company_id, first_name, email, status) "
            "VALUES (?, 'Ada', 'ada@example.com', 'NEW')",
            (company_id,),
        )

    alembic(database, "upgrade", "head")
    assert current_revision(database) == _REVISION
    contact_columns = columns(database, "contacts")
    candidate_columns = columns(database, "contact_discovery_candidates")
    assert contact_columns["first_name"][3] == 0
    assert {"linkedin_url", "instagram_url"} <= contact_columns.keys()
    assert {"linkedin_url", "instagram_url"} <= candidate_columns.keys()
    assert "ck_contacts_meaningful_identity" in table_sql(database, "contacts")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT first_name, email FROM contacts WHERE email = 'ada@example.com'"
        ).fetchone() == ("Ada", "ada@example.com")
        connection.execute(
            "INSERT INTO contacts (company_id, first_name, email, status) "
            "VALUES (?, NULL, 'info@example.com', 'NEW')",
            (company_id,),
        )
        assert connection.execute(
            "SELECT first_name FROM contacts WHERE email = 'info@example.com'"
        ).fetchone() == (None,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO contacts "
                "(company_id, first_name, last_name, email, phone, linkedin_url, "
                "instagram_url, status) VALUES (?, ' ', '', NULL, ' ', NULL, '', 'NEW')",
                (company_id,),
            )


def test_safe_downgrade_and_reupgrade_without_generic_contacts(tmp_path: Path) -> None:
    database = tmp_path / "f7h0-round-trip.sqlite"
    alembic(database, "upgrade", "head")
    company_id = create_company(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO contacts (company_id, first_name, email, instagram_url, status) "
            "VALUES (?, 'Ada', 'ada@example.com', 'https://www.instagram.com/ada', 'NEW')",
            (company_id,),
        )

    alembic(database, "downgrade", _PREVIOUS_REVISION)
    assert current_revision(database) == _PREVIOUS_REVISION
    contact_columns = columns(database, "contacts")
    assert contact_columns["first_name"][3] == 1
    assert "instagram_url" not in contact_columns
    assert "linkedin_url" in contact_columns
    assert "ck_contacts_meaningful_identity" not in table_sql(database, "contacts")
    candidate_columns = columns(database, "contact_discovery_candidates")
    assert "linkedin_url" not in candidate_columns
    assert "instagram_url" not in candidate_columns
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT first_name, email FROM contacts").fetchone() == (
            "Ada",
            "ada@example.com",
        )

    alembic(database, "upgrade", "head")
    assert current_revision(database) == _REVISION


def test_upgrade_refuses_invalid_legacy_contact_before_schema_changes(tmp_path: Path) -> None:
    database = tmp_path / "f7h0-upgrade-refusal.sqlite"
    alembic(database, "upgrade", _PREVIOUS_REVISION)
    company_id = create_company(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO contacts "
            "(company_id, first_name, last_name, email, phone, linkedin_url, status) "
            "VALUES (?, ' ', '', NULL, ' ', NULL, 'NEW')",
            (company_id,),
        )

    result = alembic(database, "upgrade", "head", check=False)
    assert result.returncode != 0
    assert _UPGRADE_ERROR in result.stderr
    assert current_revision(database) == _PREVIOUS_REVISION
    assert "instagram_url" not in columns(database, "contacts")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT first_name, last_name, email, phone FROM contacts"
        ).fetchone() == (" ", "", None, " ")


def test_downgrade_refuses_to_fabricate_or_delete_generic_contacts(tmp_path: Path) -> None:
    database = tmp_path / "f7h0-refusal.sqlite"
    alembic(database, "upgrade", "head")
    company_id = create_company(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO contacts (company_id, first_name, email, status) "
            "VALUES (?, NULL, 'info@example.com', 'NEW')",
            (company_id,),
        )

    result = alembic(database, "downgrade", _PREVIOUS_REVISION, check=False)
    assert result.returncode != 0
    assert _DOWNGRADE_ERROR in result.stderr
    assert current_revision(database) == _REVISION
    assert "instagram_url" in columns(database, "contacts")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT first_name, email FROM contacts").fetchone() == (
            None,
            "info@example.com",
        )

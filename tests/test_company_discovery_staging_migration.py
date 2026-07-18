import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RUN_TABLE = "company_discovery_runs"
_CANDIDATE_TABLE = "company_discovery_candidates"


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


def table_names(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def current_revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def column_default(database: Path, table: str, column: str) -> str | None:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(f"PRAGMA table_info('{table}')").fetchall()
    return next((str(row[4]).strip("'") for row in rows if row[1] == column), None)


def test_f7b_migration_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "f7b-lifecycle.sqlite"
    alembic(database, "upgrade", "6f1a2b3c4d5e")
    assert not {_RUN_TABLE, _CANDIDATE_TABLE} & table_names(database)

    alembic(database, "upgrade", "7b4c5d6e7f80")
    assert {_RUN_TABLE, _CANDIDATE_TABLE} <= table_names(database)
    assert current_revision(database) == "7b4c5d6e7f80"
    assert column_default(database, _RUN_TABLE, "run_status") == "PENDING"
    assert column_default(database, _CANDIDATE_TABLE, "candidate_status") == "DISCOVERED"
    with sqlite3.connect(database) as connection:
        run_indexes = {row[1] for row in connection.execute(f"PRAGMA index_list('{_RUN_TABLE}')")}
        candidate_indexes = {
            row[1] for row in connection.execute(f"PRAGMA index_list('{_CANDIDATE_TABLE}')")
        }
    assert "ix_company_discovery_runs_project_status" in run_indexes
    assert "ix_company_discovery_runs_project_fingerprint" in run_indexes
    assert "ix_company_discovery_candidates_project_status" in candidate_indexes
    assert any(
        name.startswith("sqlite_autoindex_company_discovery_candidates")
        for name in candidate_indexes
    )

    alembic(database, "downgrade", "6f1a2b3c4d5e")
    remaining = table_names(database)
    assert not {_RUN_TABLE, _CANDIDATE_TABLE} & remaining
    assert {"projects", "companies", "search_profiles", "contact_discovery_candidates"} <= remaining
    assert current_revision(database) == "6f1a2b3c4d5e"

    alembic(database, "upgrade", "head")
    assert {_RUN_TABLE, _CANDIDATE_TABLE} <= table_names(database)
    assert current_revision(database) == "7b4c5d6e7f80"
    _assert_direct_sql_defaults(database)


def test_full_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "f7b-full-round-trip.sqlite"
    alembic(database, "upgrade", "head")
    assert current_revision(database) == "7b4c5d6e7f80"
    alembic(database, "downgrade", "base")
    assert table_names(database) <= {"alembic_version"}
    alembic(database, "upgrade", "head")
    assert current_revision(database) == "7b4c5d6e7f80"
    assert {_RUN_TABLE, _CANDIDATE_TABLE} <= table_names(database)


def _assert_direct_sql_defaults(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        project_id = connection.execute(
            "INSERT INTO projects (name) VALUES ('Migration')"
        ).lastrowid
        run_id = connection.execute(
            "INSERT INTO company_discovery_runs "
            "(project_id, provider, request_fingerprint, request_snapshot, started_at, "
            "created_at, updated_at) VALUES (?, 'serpapi', ?, '{}', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (project_id, "a" * 64),
        ).lastrowid
        connection.execute(
            "INSERT INTO company_discovery_candidates "
            "(project_id, first_seen_run_id, last_seen_run_id, provider, identity_key, "
            "created_at, updated_at) VALUES (?, ?, ?, 'serpapi', 'website:example.com', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (project_id, run_id, run_id),
        )
        assert connection.execute(
            "SELECT run_status FROM company_discovery_runs WHERE id = ?", (run_id,)
        ).fetchone() == ("PENDING",)
        assert connection.execute(
            "SELECT candidate_status FROM company_discovery_candidates"
        ).fetchone() == ("DISCOVERED",)

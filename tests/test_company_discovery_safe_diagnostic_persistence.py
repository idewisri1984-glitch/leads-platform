import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.database.base import Base
from app.modules.company_discovery.models import CompanyDiscoveryRun
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
    CompanyDiscoverySourceMode,
)
from app.modules.project.models import Project

_ROOT = Path(__file__).resolve().parents[1]
_PREVIOUS_HEAD = "c71e3a9d4f20"
_HEAD = "e41f7a9c2b60"


def _alembic(database: Path, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    environment["DEBUG"] = "false"
    subprocess.run(
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


def test_safe_diagnostic_migration_round_trip_preserves_existing_run(tmp_path: Path) -> None:
    database = tmp_path / "diagnostic-migration.sqlite3"
    _alembic(database, "upgrade", _PREVIOUS_HEAD)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO projects (name) VALUES ('Project')")
        project_id = connection.execute("SELECT id FROM projects").fetchone()[0]
        connection.execute(
            "INSERT INTO company_discovery_runs "
            "(project_id, provider, run_status, request_fingerprint, request_snapshot, "
            "query_count, result_count, candidate_count, started_at, error_code, "
            "created_at, updated_at) VALUES (?, 'serpapi', 'FAILED', ?, '{}', 1, 0, 0, "
            "CURRENT_TIMESTAMP, 'request_error', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (project_id, "a" * 64),
        )
        connection.commit()

    _alembic(database, "upgrade", _HEAD)
    assert _revision(database) == _HEAD
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(company_discovery_runs)")
        }
        assert {"error_subtype", "error_http_status"} <= columns
        assert connection.execute(
            "SELECT error_code, error_subtype, error_http_status FROM company_discovery_runs"
        ).fetchone() == ("request_error", None, None)
        connection.execute(
            "UPDATE company_discovery_runs SET error_subtype='HTTP_CLIENT', error_http_status=400"
        )
        connection.commit()

    _alembic(database, "downgrade", _PREVIOUS_HEAD)
    assert _revision(database) == _PREVIOUS_HEAD
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(company_discovery_runs)")
        }
        assert "error_subtype" not in columns
        assert "error_http_status" not in columns
        assert connection.execute("SELECT error_code FROM company_discovery_runs").fetchone() == (
            "request_error",
        )

    _alembic(database, "upgrade", _HEAD)
    assert _revision(database) == _HEAD
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT error_code, error_subtype, error_http_status FROM company_discovery_runs"
        ).fetchone() == ("request_error", None, None)


@pytest.mark.parametrize(
    ("subtype", "status"),
    [("TRANSPORT", None), ("HTTP_CLIENT", 400), ("VALIDATION", None)],
)
def test_safe_diagnostic_survives_fresh_session(
    tmp_path: Path,
    subtype: str,
    status: int | None,
) -> None:
    database = tmp_path / f"fresh-{subtype}.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            project = Project(name="Project")
            session.add(project)
            session.flush()
            repository = CompanyDiscoveryStagingRepository(session)
            run = repository.create_run(
                CompanyDiscoveryRunCreate(
                    project_id=project.id,
                    provider="serpapi",
                    request_snapshot=CompanyDiscoveryRequestSnapshot(
                        source_mode=CompanyDiscoverySourceMode.AD_HOC,
                        result_limit=5,
                        total_result_ceiling=5,
                    ),
                )
            )
            repository.update_run(
                run.id,
                CompanyDiscoveryRunUpdate(
                    run_status="FAILED",
                    query_count=1,
                    result_count=0,
                    candidate_count=0,
                    error_code="request_error",
                    error_subtype=subtype,
                    error_http_status=status,
                ),
            )
            run_id = run.id
            session.commit()

        with Session(engine) as fresh_session:
            persisted = fresh_session.get(CompanyDiscoveryRun, run_id)
            assert persisted is not None
            assert persisted.error_code == "request_error"
            assert persisted.error_subtype == subtype
            assert persisted.error_http_status == status
            serialized = repr(
                (persisted.error_code, persisted.error_subtype, persisted.error_http_status)
            )
            assert "api_key" not in serialized
            assert "response-body-secret" not in serialized
    finally:
        engine.dispose()


def test_success_run_diagnostic_columns_are_nullable(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'success.sqlite3'}")
    Base.metadata.create_all(engine)
    try:
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("company_discovery_runs")
        }
        assert columns["error_subtype"]["nullable"] is True
        assert columns["error_http_status"]["nullable"] is True
    finally:
        engine.dispose()

from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryRun,
)
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as value:
        yield value


def create_project_run(session: Session) -> tuple[Project, CompanyDiscoveryRun]:
    project = Project(name="Project")
    session.add(project)
    session.flush()
    run = CompanyDiscoveryRun(
        project_id=project.id,
        provider="serpapi",
        request_fingerprint="a" * 64,
        request_snapshot={"source_mode": "AD_HOC"},
    )
    session.add(run)
    session.flush()
    return project, run


def candidate(project_id: int, run_id: int, **changes: object) -> CompanyDiscoveryCandidate:
    values: dict[str, object] = {
        "project_id": project_id,
        "first_seen_run_id": run_id,
        "last_seen_run_id": run_id,
        "provider": "serpapi",
        "identity_key": "website:example.com",
    }
    values.update(changes)
    return CompanyDiscoveryCandidate(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_status": "UNKNOWN"},
        {"query_count": -1},
        {"result_count": -1},
        {"candidate_count": -1},
    ],
)
def test_run_database_constraints(session: Session, changes: dict[str, object]) -> None:
    project = Project(name="Project")
    session.add(project)
    session.flush()
    values: dict[str, object] = {
        "project_id": project.id,
        "provider": "serpapi",
        "request_fingerprint": "a" * 64,
        "request_snapshot": {"source_mode": "AD_HOC"},
    }
    values.update(changes)
    session.add(CompanyDiscoveryRun(**values))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate_status": "UNKNOWN"},
        {"best_position": 0},
        {"country_code": "us"},
        {"country_code": "U1"},
    ],
)
def test_candidate_database_constraints(session: Session, changes: dict[str, object]) -> None:
    project, run = create_project_run(session)
    session.add(candidate(project.id, run.id, **changes))
    with pytest.raises(IntegrityError):
        session.flush()


def test_candidate_foreign_keys_and_project_unique_identity(session: Session) -> None:
    project, run = create_project_run(session)
    session.add_all([candidate(project.id, run.id), candidate(project.id, run.id)])
    with pytest.raises(IntegrityError):
        session.flush()


def test_candidate_run_and_promoted_company_foreign_keys(session: Session) -> None:
    project, run = create_project_run(session)
    session.add(candidate(project.id, run.id + 1000, identity_key="website:missing-run.example"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    project, run = create_project_run(session)
    session.add(
        candidate(
            project.id,
            run.id,
            identity_key="website:missing-company.example",
            promoted_company_id=999999,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_valid_promoted_company_foreign_key(session: Session) -> None:
    project, run = create_project_run(session)
    company = Company(project_id=project.id, name="Canonical")
    session.add(company)
    session.flush()
    item = candidate(project.id, run.id, promoted_company_id=company.id)
    session.add(item)
    session.flush()
    assert item.promoted_company_id == company.id


def test_database_supplies_run_status_default_for_direct_sql(session: Session) -> None:
    project = Project(name="Direct SQL Project")
    session.add(project)
    session.flush()
    run_id = session.scalar(
        text(
            "INSERT INTO company_discovery_runs "
            "(project_id, provider, request_fingerprint, request_snapshot, started_at, "
            "query_count, result_count, candidate_count, created_at, updated_at) VALUES "
            "(:project_id, 'serpapi', :fingerprint, :snapshot, CURRENT_TIMESTAMP, "
            "0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"
        ),
        {"project_id": project.id, "fingerprint": "a" * 64, "snapshot": "{}"},
    )
    assert run_id is not None
    status = session.scalar(
        text("SELECT run_status FROM company_discovery_runs WHERE id = :id"), {"id": run_id}
    )
    assert status == "PENDING"


def test_database_supplies_candidate_status_default_for_direct_sql(session: Session) -> None:
    project, run = create_project_run(session)
    candidate_id = session.scalar(
        text(
            "INSERT INTO company_discovery_candidates "
            "(project_id, first_seen_run_id, last_seen_run_id, provider, identity_key, "
            "created_at, updated_at) VALUES "
            "(:project_id, :run_id, :run_id, 'serpapi', 'website:direct.example', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"
        ),
        {"project_id": project.id, "run_id": run.id},
    )
    assert candidate_id is not None
    status = session.scalar(
        text("SELECT candidate_status FROM company_discovery_candidates WHERE id = :id"),
        {"id": candidate_id},
    )
    assert status == "DISCOVERED"

from collections.abc import Generator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_discovery.candidate_review import (
    CompanyDiscoveryCandidateReviewService,
    CompanyDiscoveryCandidateTransitionError,
)
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryCandidateStatus,
)
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
)
from app.modules.contact.models import Contact
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as value:
        yield value


def make_project(session: Session, name: str = "Project") -> Project:
    project = Project(name=name)
    session.add(project)
    session.flush()
    return project


def run_data(project_id: int) -> CompanyDiscoveryRunCreate:
    return CompanyDiscoveryRunCreate(
        project_id=project_id,
        search_profile_id=None,
        provider="serpapi",
        request_snapshot=CompanyDiscoveryRequestSnapshot(
            source_mode="AD_HOC",
            country_codes=[],
            query_count=1,
            result_limit=10,
            total_result_ceiling=10,
        ),
    )


def candidate_data(
    project_id: int, run_id: int, **changes: object
) -> CompanyDiscoveryCandidateCreate:
    values: dict[str, object] = {
        "project_id": project_id,
        "run_id": run_id,
        "provider": "serpapi",
        "name": "Acme",
        "website": "https://www.example.com/about",
        "country_code": "US",
        "position": 9,
    }
    values.update(changes)
    return CompanyDiscoveryCandidateCreate(**values)


def test_reviewed_candidate_persists_after_commit(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id, first_run.id, candidate_data(project.id, first_run.id)
    )
    service = CompanyDiscoveryCandidateReviewService(repository)
    candidate = service.mark_reviewed(project.id, created.candidate.id)
    assert candidate.current_status == CompanyDiscoveryCandidateStatus.REVIEWED

    session.commit()
    session.expunge_all()

    read_back = session.get(CompanyDiscoveryCandidate, candidate.candidate.id)
    assert read_back is not None
    assert read_back.candidate_status == CompanyDiscoveryCandidateStatus.REVIEWED


def test_candidate_status_rollback_uncommitted_change(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id, first_run.id, candidate_data(project.id, first_run.id)
    )
    service = CompanyDiscoveryCandidateReviewService(repository)
    session.commit()

    result = service.reject(project.id, created.candidate.id)
    assert result.current_status == CompanyDiscoveryCandidateStatus.REJECTED

    session.rollback()
    session.commit()
    candidate_row = repository.get_candidate(created.candidate.id)
    assert candidate_row is not None
    assert candidate_row.candidate_status == CompanyDiscoveryCandidateStatus.DISCOVERED


def test_idempotent_candidate_review_does_not_affect_other_fields(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id,
        first_run.id,
        candidate_data(project.id, first_run.id, name="Acme", position=12),
    )
    candidate_row = repository.get_candidate(created.candidate.id)
    assert candidate_row is not None
    candidate_row.name = "Manual Name"
    candidate_row.best_position = 11
    candidate_row.candidate_status = CompanyDiscoveryCandidateStatus.REVIEWED
    session.flush()
    service = CompanyDiscoveryCandidateReviewService(repository)
    changed_before = candidate_row.updated_at

    result = service.mark_reviewed(project.id, candidate_row.id)
    assert result.changed is False

    session.commit()
    session.refresh(candidate_row)

    assert candidate_row.candidate_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert candidate_row.best_position == 11
    assert candidate_row.name == "Manual Name"
    assert candidate_row.updated_at.replace(tzinfo=None) == changed_before.replace(tzinfo=None)


def test_forbidden_transition_keeps_candidate_unchanged(session: Session) -> None:
    project = make_project(session)
    company = Company(project_id=project.id, name="Candidate Company")
    session.add(company)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id,
        first_run.id,
        candidate_data(project.id, first_run.id, name="Acme"),
    )
    candidate_row = repository.get_candidate(created.candidate.id)
    assert candidate_row is not None
    candidate_row.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
    candidate_row.promoted_company_id = company.id
    session.flush()
    service = CompanyDiscoveryCandidateReviewService(repository)

    with pytest.raises(CompanyDiscoveryCandidateTransitionError):
        service.mark_reviewed(project.id, candidate_row.id)

    assert candidate_row.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED
    assert candidate_row.promoted_company_id == company.id


@pytest.mark.parametrize(
    "status",
    [
        CompanyDiscoveryCandidateStatus.REVIEWED,
        CompanyDiscoveryCandidateStatus.REJECTED,
        CompanyDiscoveryCandidateStatus.PROMOTED,
    ],
)
def test_rediscovery_preserves_review_or_reject_or_promoted_state(
    session: Session,
    status: CompanyDiscoveryCandidateStatus,
) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    service = CompanyDiscoveryCandidateReviewService(repository)

    first_run = repository.create_run(run_data(project.id))
    second_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id,
        first_run.id,
        candidate_data(project.id, first_run.id, position=9),
    )
    row = repository.get_candidate(created.candidate.id)
    assert row is not None
    first_seen = row.first_seen_run_id
    assert row.best_position == 9

    if status == CompanyDiscoveryCandidateStatus.PROMOTED:
        company = Company(project_id=project.id, name="Canonical")
        session.add(company)
        session.flush()
        row.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
        row.promoted_company_id = company.id
        session.flush()
    elif status == CompanyDiscoveryCandidateStatus.REVIEWED:
        service.mark_reviewed(project.id, row.id)
    else:
        service.reject(project.id, row.id)

    session.flush()
    session.commit()

    repository.upsert_candidate(
        project.id,
        second_run.id,
        candidate_data(
            project.id,
            second_run.id,
            name="Acme Revisited",
            website="https://example.com/updated",
            position=2,
        ),
    )

    session.refresh(row)
    assert row.candidate_status == status
    assert row.first_seen_run_id == first_seen
    assert row.last_seen_run_id == second_run.id
    assert row.best_position == 2
    if status == CompanyDiscoveryCandidateStatus.PROMOTED:
        assert row.promoted_company_id is not None


def test_candidate_review_does_not_create_company_or_contact_records(session: Session) -> None:
    project = make_project(session)
    before_company_count = session.scalar(select(func.count()).select_from(Company))
    before_contact_count = session.scalar(select(func.count()).select_from(Contact))
    repository = CompanyDiscoveryStagingRepository(session)
    service = CompanyDiscoveryCandidateReviewService(repository)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))
    service.mark_reviewed(project.id, created.candidate.id)

    assert session.scalar(select(func.count()).select_from(Company)) == before_company_count
    assert session.scalar(select(func.count()).select_from(Contact)) == before_contact_count


def test_integration_does_not_clear_promoted_company_id_on_review(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))
    row = repository.get_candidate(created.candidate.id)
    assert row is not None
    company = Company(project_id=project.id, name="Existing")
    session.add(company)
    session.flush()
    row.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
    row.promoted_company_id = company.id
    session.flush()
    service = CompanyDiscoveryCandidateReviewService(repository)

    with pytest.raises(CompanyDiscoveryCandidateTransitionError):
        service.mark_reviewed(project.id, row.id)

    assert row.promoted_company_id == company.id
    assert row.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED


def test_commit_or_rollback_count_for_candidate_mutation_via_service(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))
    service = CompanyDiscoveryCandidateReviewService(repository)

    result = service.mark_reviewed(project.id, created.candidate.id)
    assert result.current_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert result.changed is True

    committed = result.candidate.id
    session.commit()
    verification = SessionLocal()
    with verification as verify:
        check = verify.get(CompanyDiscoveryCandidate, committed)
        assert check is not None

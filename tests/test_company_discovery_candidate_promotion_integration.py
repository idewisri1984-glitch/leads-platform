from collections.abc import Generator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.company_discovery import (
    CompanyDiscoveryCandidateNotEligibleError,
    CompanyDiscoveryCandidatePromotionNotFoundError,
    CompanyDiscoveryCandidatePromotionService,
    CompanyDiscoveryCandidateReviewService,
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


def make_candidate(
    session: Session,
    project: Project,
    *,
    website: str | None = "https://www.example.com/about",
    reviewed: bool = True,
) -> CompanyDiscoveryCandidate:
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(
        CompanyDiscoveryRunCreate(
            project_id=project.id,
            search_profile_id=None,
            provider="serpapi",
            request_snapshot=CompanyDiscoveryRequestSnapshot(
                source_mode="AD_HOC",
                country_codes=["US"],
                query_count=1,
                result_limit=10,
                total_result_ceiling=10,
            ),
        )
    )
    created = repository.upsert_candidate(
        project.id,
        run.id,
        CompanyDiscoveryCandidateCreate(
            project_id=project.id,
            run_id=run.id,
            provider="serpapi",
            name="Acme",
            website=website,
            country_code="US",
            position=1,
        ),
    )
    if reviewed:
        CompanyDiscoveryCandidateReviewService(repository).mark_reviewed(
            project.id,
            created.candidate.id,
        )
    candidate = repository.get_candidate(created.candidate.id)
    assert candidate is not None
    return candidate


def make_service(session: Session) -> CompanyDiscoveryCandidatePromotionService:
    return CompanyDiscoveryCandidatePromotionService(
        CompanyDiscoveryStagingRepository(session),
        CompanyRepository(session),
    )


def test_new_company_and_candidate_link_persist_after_caller_commit(session: Session) -> None:
    project = make_project(session)
    candidate = make_candidate(session, project)
    contact_count = session.scalar(select(func.count()).select_from(Contact))

    result = make_service(session).promote(project.id, candidate.id)
    session.commit()
    session.expire_all()

    stored_candidate = session.get_one(CompanyDiscoveryCandidate, candidate.id)
    stored_company = session.get_one(Company, result.company_id)
    assert result.created_company is True and result.changed is True
    assert stored_candidate.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED
    assert stored_candidate.promoted_company_id == stored_company.id
    assert stored_company.project_id == stored_candidate.project_id == project.id
    assert stored_company.name == "Acme"
    assert stored_company.website == "https://www.example.com/about"
    assert stored_company.country == "US"
    assert stored_company.status == "NEW"
    assert stored_company.city is None
    assert stored_company.industry is None
    assert stored_company.notes is None
    assert session.scalar(select(func.count()).select_from(Contact)) == contact_count


def test_caller_rollback_removes_company_and_candidate_changes(session: Session) -> None:
    project = make_project(session)
    candidate = make_candidate(session, project)
    project_id, candidate_id = project.id, candidate.id
    session.commit()

    result = make_service(session).promote(project_id, candidate_id)
    assert session.get(Company, result.company_id) is not None
    session.rollback()
    session.expire_all()

    stored_candidate = session.get_one(CompanyDiscoveryCandidate, candidate_id)
    assert stored_candidate.candidate_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert stored_candidate.promoted_company_id is None
    assert session.get(Company, result.company_id) is None


def test_existing_website_duplicate_is_reused_and_second_call_is_idempotent(
    session: Session,
) -> None:
    project = make_project(session)
    existing = Company(
        project_id=project.id,
        name="Existing Name",
        website="EXAMPLE.COM",
        country="GB",
    )
    session.add(existing)
    candidate = make_candidate(session, project)
    session.commit()

    service = make_service(session)
    first = service.promote(project.id, candidate.id)
    session.commit()
    second = service.promote(project.id, candidate.id)
    session.commit()

    assert first.company_id == existing.id
    assert first.created_company is False and first.changed is True
    assert second.company_id == existing.id
    assert second.created_company is False and second.changed is False
    assert session.scalar(select(func.count()).select_from(Company)) == 1
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_forbidden_promotion_leaves_database_unchanged(session: Session) -> None:
    project = make_project(session)
    candidate = make_candidate(session, project, reviewed=False)
    project_id, candidate_id = project.id, candidate.id
    session.commit()

    with pytest.raises(CompanyDiscoveryCandidateNotEligibleError):
        make_service(session).promote(project_id, candidate_id)
    session.rollback()

    stored = session.get_one(CompanyDiscoveryCandidate, candidate_id)
    assert stored.candidate_status == CompanyDiscoveryCandidateStatus.DISCOVERED
    assert stored.promoted_company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_cross_project_candidate_is_not_promoted(session: Session) -> None:
    first = make_project(session, "First")
    second = make_project(session, "Second")
    candidate = make_candidate(session, first)
    session.commit()

    with pytest.raises(CompanyDiscoveryCandidatePromotionNotFoundError):
        make_service(session).promote(second.id, candidate.id)
    session.rollback()

    stored = session.get_one(CompanyDiscoveryCandidate, candidate.id)
    assert stored.candidate_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert stored.promoted_company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 0

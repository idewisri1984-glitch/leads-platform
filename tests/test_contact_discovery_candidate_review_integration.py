from collections.abc import Generator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact_discovery import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateReviewNotFoundError,
    ContactDiscoveryCandidateReviewService,
    ContactDiscoveryCandidateStatus,
    ContactDiscoveryRepository,
    ContactDiscoverySourceType,
)
from app.modules.contact_discovery.models import ContactDiscoveryCandidate
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as value:
        yield value


def setup_candidate(
    session: Session, *, project_name: str = "Project", company_name: str = "Company"
) -> tuple[Company, ContactDiscoveryCandidate]:
    project = Project(name=project_name)
    session.add(project)
    session.flush()
    company = Company(project_id=project.id, name=company_name)
    session.add(company)
    session.flush()
    repository = ContactDiscoveryRepository(session)
    result = repository.upsert_candidate(
        company.id,
        ContactDiscoveryCandidateCreate(
            company_id=company.id,
            name="Person",
            title="Director",
            email=f"{company.id}@example.com",
            phone="+1 555 0100",
            source_url="https://example.com/team",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
            confidence=80,
        ),
    )
    candidate = repository.get_candidate(result.candidate.id)
    assert candidate is not None
    return company, candidate


@pytest.mark.parametrize(
    ("initial", "operation", "expected"),
    [
        ("DISCOVERED", "review", "REVIEWED"),
        ("DISCOVERED", "reject", "REJECTED"),
        ("REVIEWED", "reject", "REJECTED"),
    ],
)
def test_caller_commit_persists_transitions(
    session: Session, initial: str, operation: str, expected: str
) -> None:
    company, candidate = setup_candidate(session)
    candidate.discovery_status = ContactDiscoveryCandidateStatus(initial)
    session.commit()
    service = ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session))
    if operation == "review":
        service.mark_reviewed(company.id, candidate.id)
    else:
        service.reject(company.id, candidate.id)
    session.commit()
    session.expire_all()
    assert session.get(ContactDiscoveryCandidate, candidate.id).discovery_status == expected


@pytest.mark.parametrize("status", ["REVIEWED", "REJECTED"])
def test_idempotent_transition_does_not_dirty_candidate(session: Session, status: str) -> None:
    company, candidate = setup_candidate(session)
    candidate.discovery_status = ContactDiscoveryCandidateStatus(status)
    session.commit()
    service = ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session))
    result = (
        service.mark_reviewed(company.id, candidate.id)
        if status == "REVIEWED"
        else service.reject(company.id, candidate.id)
    )
    assert result.changed is False
    assert not session.dirty


def test_caller_rollback_restores_flushed_transition(session: Session) -> None:
    company, candidate = setup_candidate(session)
    session.commit()
    ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session)).mark_reviewed(
        company.id, candidate.id
    )
    session.rollback()
    assert session.get(ContactDiscoveryCandidate, candidate.id).discovery_status == "DISCOVERED"


def test_cross_company_scope_does_not_modify_candidate(session: Session) -> None:
    first, candidate = setup_candidate(session, project_name="First", company_name="First")
    other, _ = setup_candidate(session, project_name="Other", company_name="Other")
    service = ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session))
    with pytest.raises(ContactDiscoveryCandidateReviewNotFoundError):
        service.mark_reviewed(other.id, candidate.id)
    assert candidate.company_id == first.id
    assert candidate.discovery_status == "DISCOVERED"


def test_review_preserves_descriptive_rows_and_never_creates_contact(session: Session) -> None:
    company, candidate = setup_candidate(session)
    session.commit()
    snapshot = (
        company.name,
        candidate.name,
        candidate.title,
        candidate.email,
        candidate.phone,
        candidate.source_url,
        candidate.confidence,
    )
    ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session)).reject(
        company.id, candidate.id
    )
    session.commit()
    assert snapshot == (
        company.name,
        candidate.name,
        candidate.title,
        candidate.email,
        candidate.phone,
        candidate.source_url,
        candidate.confidence,
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_discovery_upsert_remains_protected_after_review(session: Session) -> None:
    company, candidate = setup_candidate(session)
    service = ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session))
    service.mark_reviewed(company.id, candidate.id)
    result = ContactDiscoveryRepository(session).upsert_candidate(
        company.id,
        ContactDiscoveryCandidateCreate(
            company_id=company.id,
            name="Replacement",
            title="Changed",
            email=candidate.email,
            source_type=ContactDiscoverySourceType.OTHER_PUBLIC_PAGE,
            confidence=100,
        ),
    )
    assert result.protected is True
    assert result.candidate.name == "Person"

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


def test_failure_after_flush_then_caller_rollback_restores_persisted_state(
    session: Session,
) -> None:
    company, candidate = setup_candidate(session)
    session.commit()
    candidate_id = candidate.id
    snapshot = (
        candidate.name,
        candidate.title,
        candidate.email,
        candidate.phone,
        candidate.source_url,
        candidate.confidence,
    )

    try:
        ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session)).mark_reviewed(
            company.id, candidate_id
        )
        assert session.get(ContactDiscoveryCandidate, candidate_id).discovery_status == "REVIEWED"
        raise RuntimeError("synthetic post-flush failure")
    except RuntimeError:
        session.rollback()

    with SessionLocal() as fresh:
        restored = fresh.get(ContactDiscoveryCandidate, candidate_id)
        assert restored is not None
        assert restored.discovery_status == "DISCOVERED"
        assert snapshot == (
            restored.name,
            restored.title,
            restored.email,
            restored.phone,
            restored.source_url,
            restored.confidence,
        )
        assert fresh.scalar(select(func.count()).select_from(Contact)) == 0


def test_cross_project_company_scope_hides_and_preserves_candidate(session: Session) -> None:
    first, _ = setup_candidate(session, project_name="Project A", company_name="Company A")
    second, candidate = setup_candidate(
        session,
        project_name="Project B",
        company_name="Company B",
    )
    session.commit()
    company_snapshot = (second.project_id, second.name, second.website, second.status)
    candidate_snapshot = (
        candidate.discovery_status,
        candidate.name,
        candidate.title,
        candidate.email,
        candidate.phone,
        candidate.source_url,
    )
    service = ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session))

    with pytest.raises(
        ContactDiscoveryCandidateReviewNotFoundError,
        match=r"^Candidate was not found\.$",
    ):
        service.mark_reviewed(first.id, candidate.id)

    assert company_snapshot == (second.project_id, second.name, second.website, second.status)
    assert candidate_snapshot == (
        candidate.discovery_status,
        candidate.name,
        candidate.title,
        candidate.email,
        candidate.phone,
        candidate.source_url,
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_existing_canonical_contact_is_unchanged_by_candidate_review(session: Session) -> None:
    company, candidate = setup_candidate(session)
    contact = Contact(
        company_id=company.id,
        first_name="Existing",
        last_name="Person",
        job_title="Manager",
        email="existing@example.com",
        phone="+1 555 0199",
        linkedin_url="https://linkedin.com/in/existing",
        country="US",
        city="Boston",
        source="MANUAL",
        external_id="existing-1",
        status="ACTIVE",
        notes="preserve",
    )
    session.add(contact)
    session.commit()
    contact_id = contact.id
    snapshot = (
        contact.company_id,
        contact.first_name,
        contact.last_name,
        contact.job_title,
        contact.email,
        contact.phone,
        contact.linkedin_url,
        contact.country,
        contact.city,
        contact.source,
        contact.external_id,
        contact.status,
        contact.notes,
    )

    ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session)).reject(
        company.id, candidate.id
    )
    session.commit()
    session.expire_all()
    reloaded = session.get(Contact, contact_id)
    assert reloaded is not None
    assert snapshot == (
        reloaded.company_id,
        reloaded.first_name,
        reloaded.last_name,
        reloaded.job_title,
        reloaded.email,
        reloaded.phone,
        reloaded.linkedin_url,
        reloaded.country,
        reloaded.city,
        reloaded.source,
        reloaded.external_id,
        reloaded.status,
        reloaded.notes,
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_review_and_reject_do_not_invoke_discovery_stack(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.contact_discovery import website_contact_parser
    from app.modules.contact_discovery.service import ContactDiscoveryService
    from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProvider
    from app.providers.public_web_fetcher import BoundedPublicWebFetcher

    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("discovery stack invoked")

    monkeypatch.setattr(ContactDiscoveryService, "run", forbidden)
    monkeypatch.setattr(WebsiteContactDiscoveryProvider, "discover", forbidden)
    monkeypatch.setattr(
        website_contact_parser,
        "parse_contact_discovery_candidates_from_html",
        forbidden,
    )
    monkeypatch.setattr(BoundedPublicWebFetcher, "fetch", forbidden)
    first_company, first_candidate = setup_candidate(session, company_name="Review")
    second_company, second_candidate = setup_candidate(session, company_name="Reject")
    service = ContactDiscoveryCandidateReviewService(ContactDiscoveryRepository(session))

    assert service.mark_reviewed(first_company.id, first_candidate.id).changed is True
    assert service.reject(second_company.id, second_candidate.id).changed is True
    session.commit()

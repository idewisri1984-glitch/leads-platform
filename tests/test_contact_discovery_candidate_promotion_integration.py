from collections.abc import Generator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.base import Base
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.contact_discovery import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateNotEligibleError,
    ContactDiscoveryCandidatePromotionConsistencyError,
    ContactDiscoveryCandidatePromotionInvalidDataError,
    ContactDiscoveryCandidatePromotionNotFoundError,
    ContactDiscoveryCandidatePromotionService,
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


def create_company(session: Session, name: str = "Company") -> Company:
    project = Project(name=f"{name} Project")
    session.add(project)
    session.flush()
    company = Company(project_id=project.id, name=name)
    session.add(company)
    session.flush()
    return company


def create_candidate(
    session: Session,
    company: Company,
    *,
    name: str = "Ada Lovelace",
    email: str | None = "ADA@example.com",
    title: str | None = "Chief Scientist",
    phone: str | None = "+1 555 0100",
    dedupe_suffix: str = "",
) -> ContactDiscoveryCandidate:
    repository = ContactDiscoveryRepository(session)
    result = repository.upsert_candidate(
        company.id,
        ContactDiscoveryCandidateCreate(
            company_id=company.id,
            name=name,
            title=title,
            email=email,
            phone=phone,
            source_url=f"https://example.com/team{dedupe_suffix}",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
            confidence=90,
        ),
    )
    record = session.get(ContactDiscoveryCandidate, result.candidate.id)
    assert record is not None
    repository.set_candidate_status(
        company.id,
        record.id,
        ContactDiscoveryCandidateStatus.REVIEWED,
    )
    return record


def promotion_service(session: Session) -> ContactDiscoveryCandidatePromotionService:
    return ContactDiscoveryCandidatePromotionService(
        ContactDiscoveryRepository(session),
        ContactRepository(session),
    )


def test_new_contact_rollback_restores_reviewed_candidate(session: Session) -> None:
    company = create_company(session)
    record = create_candidate(session, company)
    session.commit()
    company_id, candidate_id = company.id, record.id

    result = promotion_service(session).promote(company_id, candidate_id)
    assert result.previous_status is ContactDiscoveryCandidateStatus.REVIEWED
    assert result.current_status is ContactDiscoveryCandidateStatus.PROMOTED
    assert result.created_contact is True
    assert result.changed is True
    assert (
        session.get(ContactDiscoveryCandidate, candidate_id).promoted_contact_id
        == result.contact_id
    )
    session.rollback()

    with SessionLocal() as verification:
        stored = verification.get(ContactDiscoveryCandidate, candidate_id)
        assert stored is not None
        assert stored.discovery_status == ContactDiscoveryCandidateStatus.REVIEWED
        assert stored.promoted_contact_id is None
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0


def test_caller_commit_persists_exact_mapping_and_link(session: Session) -> None:
    company = create_company(session)
    record = create_candidate(
        session,
        company,
        name="  Juan   Carlos de la Vega ",
        title="  Chief   Scientist ",
        phone=" +1  (555)   0100 ",
    )
    company_snapshot = (company.project_id, company.name, company.status, company.notes)
    candidate_snapshot = (record.name, record.title, record.email, record.phone, record.source_url)
    session.commit()
    result = promotion_service(session).promote(company.id, record.id)
    contact_id = result.contact_id
    session.commit()

    with SessionLocal() as verification:
        contact = verification.get(Contact, contact_id)
        stored = verification.get(ContactDiscoveryCandidate, record.id)
        stored_company = verification.get(Company, company.id)
        assert contact is not None
        assert (
            contact.first_name,
            contact.last_name,
            contact.job_title,
            contact.email,
            contact.phone,
            contact.source,
            contact.external_id,
            contact.status,
        ) == (
            "Juan",
            "Carlos de la Vega",
            "Chief Scientist",
            "ada@example.com",
            "+1 (555) 0100",
            "CONTACT_DISCOVERY",
            f"contact-discovery-candidate:{record.id}",
            "NEW",
        )
        assert stored is not None
        assert stored.discovery_status == ContactDiscoveryCandidateStatus.PROMOTED
        assert stored.promoted_contact_id == contact_id
        assert candidate_snapshot == (
            stored.name,
            stored.title,
            stored.email,
            stored.phone,
            stored.source_url,
        )
        assert stored_company is not None
        assert company_snapshot == (
            stored_company.project_id,
            stored_company.name,
            stored_company.status,
            stored_company.notes,
        )


def test_duplicate_contact_is_reused_unchanged(session: Session) -> None:
    company = create_company(session)
    existing = Contact(
        company_id=company.id,
        first_name="Existing",
        last_name="Contact",
        email="  ADA@EXAMPLE.COM ",
        phone="keep",
        source="MANUAL",
        status="ACTIVE",
        notes="preserve",
    )
    session.add(existing)
    record = create_candidate(session, company)
    session.commit()
    snapshot = (
        existing.first_name,
        existing.last_name,
        existing.email,
        existing.phone,
        existing.source,
        existing.status,
        existing.notes,
    )
    result = promotion_service(session).promote(company.id, record.id)
    assert result.contact_id == existing.id
    assert result.created_contact is False
    assert session.scalar(select(func.count()).select_from(Contact)) == 1
    assert snapshot == (
        existing.first_name,
        existing.last_name,
        existing.email,
        existing.phone,
        existing.source,
        existing.status,
        existing.notes,
    )


def test_no_email_creates_contact_and_repeated_promotion_is_idempotent(
    session: Session,
) -> None:
    company = create_company(session)
    record = create_candidate(session, company, email=None)
    session.commit()
    first = promotion_service(session).promote(company.id, record.id)
    session.commit()
    with SessionLocal() as repeat:
        second = promotion_service(repeat).promote(company.id, record.id)
        assert second.previous_status is ContactDiscoveryCandidateStatus.PROMOTED
        assert second.current_status is ContactDiscoveryCandidateStatus.PROMOTED
        assert second.created_contact is False
        assert second.changed is False
        assert second.contact_id == first.contact_id
        assert repeat.scalar(select(func.count()).select_from(Contact)) == 1
    with SessionLocal() as verification:
        contact = verification.get(Contact, first.contact_id)
        assert contact is not None
        assert contact.email is None


def test_cross_company_and_ineligible_candidates_create_nothing(session: Session) -> None:
    first = create_company(session, "First")
    second = create_company(session, "Second")
    record = create_candidate(session, second)
    session.commit()
    with pytest.raises(ContactDiscoveryCandidatePromotionNotFoundError):
        promotion_service(session).promote(first.id, record.id)
    session.rollback()
    discovered = ContactDiscoveryRepository(session).upsert_candidate(
        first.id,
        ContactDiscoveryCandidateCreate(
            company_id=first.id,
            name="Discovered",
            source_url="https://example.com/team",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        ),
    )
    with pytest.raises(ContactDiscoveryCandidateNotEligibleError):
        promotion_service(session).promote(first.id, discovered.candidate.id)
    rejected = create_candidate(session, first, email="rejected@example.com")
    ContactDiscoveryRepository(session).set_candidate_status(
        first.id,
        rejected.id,
        ContactDiscoveryCandidateStatus.REJECTED,
    )
    with pytest.raises(ContactDiscoveryCandidateNotEligibleError):
        promotion_service(session).promote(first.id, rejected.id)
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_invalid_and_inconsistent_candidate_data_produces_no_write(session: Session) -> None:
    company = create_company(session)
    record = create_candidate(session, company)
    record.name = "   "
    session.commit()
    with pytest.raises(ContactDiscoveryCandidatePromotionInvalidDataError):
        promotion_service(session).promote(company.id, record.id)
    session.rollback()
    contact = Contact(company_id=company.id, first_name="Linked")
    session.add(contact)
    session.flush()
    record = session.get(ContactDiscoveryCandidate, record.id)
    assert record is not None
    record.name = "Ada"
    record.promoted_contact_id = contact.id
    session.commit()
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion_service(session).promote(company.id, record.id)
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_promoted_candidate_with_missing_linked_contact_is_inconsistent(
    session: Session,
) -> None:
    company = create_company(session)
    record = create_candidate(session, company)
    record.discovery_status = ContactDiscoveryCandidateStatus.PROMOTED
    record.promoted_contact_id = None
    session.commit()
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion_service(session).promote(company.id, record.id)


def test_same_company_candidates_reuse_one_contact_across_sessions(session: Session) -> None:
    company = create_company(session)
    first = create_candidate(session, company, email="same@example.com", dedupe_suffix="/one")
    second = ContactDiscoveryCandidate(
        company_id=company.id,
        name="Second Person",
        email="SAME@example.com",
        normalized_email="same@example.com",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
        confidence=80,
        discovery_status=ContactDiscoveryCandidateStatus.REVIEWED,
        deduplication_key="manual:second-same-email",
    )
    session.add(second)
    session.commit()
    company_id, first_id, second_id = company.id, first.id, second.id
    with SessionLocal() as first_session:
        first_result = promotion_service(first_session).promote(company_id, first_id)
        first_session.commit()
    with SessionLocal() as second_session:
        second_result = promotion_service(second_session).promote(company_id, second_id)
        second_session.commit()
    assert first_result.contact_id == second_result.contact_id
    assert second_result.created_contact is False
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Contact)) == 1


def test_stale_session_observes_committed_promotion_idempotently(session: Session) -> None:
    company = create_company(session)
    record = create_candidate(session, company)
    session.commit()
    company_id, candidate_id = company.id, record.id
    with SessionLocal() as stale_session, SessionLocal() as promoting_session:
        stale = stale_session.get(ContactDiscoveryCandidate, candidate_id)
        assert stale is not None
        assert stale.discovery_status == ContactDiscoveryCandidateStatus.REVIEWED
        promoted = promotion_service(promoting_session).promote(company_id, candidate_id)
        promoting_session.commit()
        repeated = promotion_service(stale_session).promote(company_id, candidate_id)
        assert repeated.changed is False
        assert repeated.contact_id == promoted.contact_id


def test_different_companies_do_not_reuse_contacts_or_touch_leads_tasks(
    session: Session,
) -> None:
    first = create_company(session, "First")
    second = create_company(session, "Second")
    first_candidate = create_candidate(session, first, email="same@example.com")
    second_candidate = create_candidate(session, second, email="same@example.com")
    first_company_snapshot = (first.name, first.status, first.notes)
    second_company_snapshot = (second.name, second.status, second.notes)
    first_result = promotion_service(session).promote(first.id, first_candidate.id)
    second_result = promotion_service(session).promote(second.id, second_candidate.id)
    assert first_result.contact_id != second_result.contact_id
    assert session.scalar(select(func.count()).select_from(Contact)) == 2
    assert (first.name, first.status, first.notes) == first_company_snapshot
    assert (second.name, second.status, second.notes) == second_company_snapshot
    for table_name in ("leads", "tasks"):
        table = Base.metadata.tables[table_name]
        assert session.scalar(select(func.count()).select_from(table)) == 0


def test_promotion_does_not_invoke_discovery_or_network(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.contact_discovery import website_contact_parser
    from app.modules.contact_discovery.service import ContactDiscoveryService
    from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProvider
    from app.providers.public_web_fetcher import BoundedPublicWebFetcher

    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("network or discovery invoked")

    monkeypatch.setattr(ContactDiscoveryService, "run", forbidden)
    monkeypatch.setattr(WebsiteContactDiscoveryProvider, "discover", forbidden)
    monkeypatch.setattr(
        website_contact_parser,
        "parse_contact_discovery_candidates_from_html",
        forbidden,
    )
    monkeypatch.setattr(BoundedPublicWebFetcher, "fetch", forbidden)
    company = create_company(session)
    record = create_candidate(session, company)
    promotion_service(session).promote(company.id, record.id)

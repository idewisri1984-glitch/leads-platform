from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact_discovery.models import (
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
    normalize_discovered_email,
    normalize_person_name,
    normalize_source_for_deduplication,
    normalize_title,
)
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as database_session:
        yield database_session


def create_project(session: Session, name: str) -> Project:
    project = Project(name=name)
    session.add(project)
    session.flush()
    return project


def create_company(session: Session, project: Project, name: str) -> Company:
    company = Company(project_id=project.id, name=name)
    session.add(company)
    session.flush()
    return company


def candidate(company_id: int, **values: object) -> ContactDiscoveryCandidateCreate:
    defaults: dict[str, object] = {
        "company_id": company_id,
        "name": "Ada Lovelace",
        "title": "Director",
        "phone": "+1 212 555 0100",
        "source_url": "https://example.com/team",
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 40,
    }
    defaults.update(values)
    return ContactDiscoveryCandidateCreate(**defaults)


def test_normalization_and_email_deduplication() -> None:
    assert normalize_person_name("  Ada   LOVELACE ") == "ada lovelace"
    assert normalize_title(" Design   Director ") == "design director"
    assert normalize_discovered_email("  ADA@Example.COM ") == "ada@example.com"
    assert (
        normalize_source_for_deduplication("HTTPS://Example.COM/team/?page=1#people")
        == "example.com/team"
    )
    assert (
        build_contact_candidate_deduplication_key(
            email=" ADA@Example.COM ", name=None, title=None, source_url=None
        )
        == "email:ada@example.com"
    )
    with pytest.raises(ValueError):
        normalize_discovered_email("ada lovelace@example.com")


def test_fallback_deduplication_ignores_query_fragment_and_text_variation() -> None:
    first = build_contact_candidate_deduplication_key(
        email=None,
        name=" Ada  Lovelace ",
        title="Design DIRECTOR",
        source_url="https://example.com/team?one=1#staff",
    )
    second = build_contact_candidate_deduplication_key(
        email=None,
        name="ada lovelace",
        title=" design director ",
        source_url="https://example.com/team?two=2",
    )
    assert first == second


@pytest.mark.parametrize(
    ("name", "title", "source_url"),
    [(None, None, "https://example.com/team"), ("Person", None, None)],
)
def test_insufficient_fallback_identity_is_rejected(
    name: str | None, title: str | None, source_url: str | None
) -> None:
    with pytest.raises(ValueError, match="insufficient"):
        build_contact_candidate_deduplication_key(
            email=None, name=name, title=title, source_url=source_url
        )


@pytest.mark.parametrize("source", ["javascript:alert(1)", "ftp://example.com/team"])
def test_unsafe_source_scheme_is_rejected(source: str) -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        normalize_source_for_deduplication(source)


def test_state_get_create_update_and_not_found_support(session: Session) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    state, created = repository.get_or_create_state(company.id)
    existing, created_again = repository.get_or_create_state(company.id)
    checked_at = datetime(2026, 7, 15, tzinfo=UTC)
    updated = repository.update_state(
        company.id,
        provider="future-website",
        discovery_status=ContactDiscoveryStatus.NOT_FOUND,
        checked_at=checked_at,
        last_error=None,
    )
    session.commit()
    assert created is True
    assert created_again is False
    assert existing.id == state.id == updated.id
    assert updated.discovery_status == ContactDiscoveryStatus.NOT_FOUND
    assert updated.checked_at is not None
    assert updated.provider == "future-website"


def test_state_and_candidate_project_lists_are_scoped_and_paginated(session: Session) -> None:
    first_project = create_project(session, "First")
    second_project = create_project(session, "Second")
    first = create_company(session, first_project, "First")
    second = create_company(session, first_project, "Second")
    other = create_company(session, second_project, "Other")
    repository = ContactDiscoveryRepository(session)
    for company in (first, second, other):
        repository.get_or_create_state(company.id)
        repository.upsert_candidate(company.id, candidate(company.id, email=f"{company.id}@x.com"))
    session.commit()
    assert [
        item.company_id for item in repository.list_states_for_project(first_project.id, 1)
    ] == [first.id]
    assert [
        item.company_id for item in repository.list_states_for_project(first_project.id, 1, 1)
    ] == [second.id]
    assert [
        item.company_id for item in repository.list_candidates_for_project(first_project.id, 10)
    ] == [first.id, second.id]
    assert [item.company_id for item in repository.list_candidates_for_company(other.id)] == [
        other.id
    ]


def test_email_upsert_normalizes_dedupes_and_fills_empty_fields(session: Session) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    first = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name=None,
            title=None,
            email=" Person@Example.COM ",
            phone=None,
            confidence=20,
        ),
    )
    second = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name="Person Name",
            title="Director",
            email="person@example.com",
            phone="+1 212 555 0100",
            confidence=80,
        ),
    )
    session.commit()
    assert first.created is True
    assert second.updated is True
    assert second.candidate.normalized_email == "person@example.com"
    assert second.candidate.name == "Person Name"
    assert second.candidate.phone == "+1 212 555 0100"
    assert second.candidate.confidence == 80
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 1


def test_social_only_upsert_serializes_and_fills_empty_social_fields(session: Session) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    first = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name=None,
            title=None,
            email="info@example.com",
            phone=None,
            linkedin_url=None,
            instagram_url=None,
        ),
    )
    second = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name=None,
            title=None,
            email="INFO@example.com",
            phone=None,
            linkedin_url="https://linkedin.com/company/example?trk=public",
            instagram_url="https://instagram.com/example?utm_source=test",
        ),
    )
    assert first.created is True
    assert second.updated is True
    assert second.candidate.linkedin_url == "https://www.linkedin.com/company/example"
    assert second.candidate.instagram_url == "https://www.instagram.com/example"


@pytest.mark.parametrize(
    "status",
    [
        ContactDiscoveryCandidateStatus.REVIEWED,
        ContactDiscoveryCandidateStatus.PROMOTED,
        ContactDiscoveryCandidateStatus.REJECTED,
    ],
)
def test_social_fields_remain_protected_after_lifecycle_transition(
    session: Session, status: ContactDiscoveryCandidateStatus
) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    created = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            email="person@example.com",
            linkedin_url="https://linkedin.com/in/original",
        ),
    )
    stored = repository.get_candidate(created.candidate.id)
    assert stored is not None
    stored.discovery_status = status
    session.flush()
    result = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            email="person@example.com",
            linkedin_url="https://linkedin.com/in/replacement",
            instagram_url="https://instagram.com/replacement",
        ),
    )
    assert result.protected is True
    assert result.updated is False
    assert result.candidate.linkedin_url == "https://www.linkedin.com/in/original"
    assert result.candidate.instagram_url is None


def test_upsert_never_replaces_values_with_null_or_lowers_confidence(session: Session) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    repository.upsert_candidate(
        company.id,
        candidate(company.id, email="person@example.com", phone="123", confidence=90),
    )
    result = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name=None,
            title=None,
            email="person@example.com",
            phone=None,
            confidence=10,
        ),
    )
    assert result.updated is False
    assert result.candidate.phone == "123"
    assert result.candidate.confidence == 90


@pytest.mark.parametrize(
    "status",
    [
        ContactDiscoveryCandidateStatus.REVIEWED,
        ContactDiscoveryCandidateStatus.PROMOTED,
        ContactDiscoveryCandidateStatus.REJECTED,
    ],
)
def test_upsert_protects_reviewed_states(
    session: Session, status: ContactDiscoveryCandidateStatus
) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    created = repository.upsert_candidate(
        company.id, candidate(company.id, email="person@example.com", confidence=20)
    )
    stored = repository.get_candidate(created.candidate.id)
    assert stored is not None
    stored.discovery_status = status
    stored.name = "Reviewed Name"
    session.flush()
    result = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name="Replacement",
            email="person@example.com",
            confidence=100,
        ),
    )
    assert result.protected is True
    assert result.updated is False
    assert result.candidate.name == "Reviewed Name"
    assert result.candidate.confidence == 20


def test_same_email_in_different_companies_is_not_global_identity(session: Session) -> None:
    project = create_project(session, "Project")
    first = create_company(session, project, "First")
    second = create_company(session, project, "Second")
    repository = ContactDiscoveryRepository(session)
    repository.upsert_candidate(first.id, candidate(first.id, email="same@example.com"))
    repository.upsert_candidate(second.id, candidate(second.id, email="SAME@example.com"))
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 2


def test_fallback_upsert_dedupes_without_email(session: Session) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    repository = ContactDiscoveryRepository(session)
    first = repository.upsert_candidate(company.id, candidate(company.id))
    second = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name=" ada   lovelace ",
            title="DIRECTOR",
            source_url="https://example.com/team?ref=nav#people",
            confidence=60,
        ),
    )
    assert first.created is True
    assert second.updated is True
    assert second.candidate.confidence == 60
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 1


def test_repository_rejects_mismatched_company_scope(session: Session) -> None:
    project = create_project(session, "Project")
    first = create_company(session, project, "First")
    second = create_company(session, project, "Second")
    with pytest.raises(ValueError, match="does not match"):
        ContactDiscoveryRepository(session).upsert_candidate(first.id, candidate(second.id))


def test_repository_never_creates_contact_records(session: Session) -> None:
    project = create_project(session, "Project")
    company = create_company(session, project, "Company")
    ContactDiscoveryRepository(session).upsert_candidate(
        company.id, candidate(company.id, email="person@example.com")
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_module_has_no_network_parser_provider_cli_or_automation_imports() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/modules/contact_discovery").glob("*.py")
    ).casefold()
    for forbidden in (
        "socket",
        "httpx",
        "requests",
        "serpapi",
        "selenium",
        "playwright",
        "send_message",
        "openai",
        "websiteenrichmentprovider",
        "htmlparser",
        "from app.modules.contact.models import contact",
    ):
        assert forbidden not in source

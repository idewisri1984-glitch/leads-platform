from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.schemas import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateRead,
    ContactDiscoveryCandidateUpdate,
    ContactDiscoveryStateCreate,
)
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as database_session:
        yield database_session


def create_company(session: Session) -> Company:
    project = Project(name="Contact Discovery Project")
    session.add(project)
    session.flush()
    company = Company(project_id=project.id, name="Company")
    session.add(company)
    session.flush()
    return company


def candidate(company_id: int, **values: object) -> ContactDiscoveryCandidate:
    defaults: dict[str, object] = {
        "company_id": company_id,
        "name": "Ada Lovelace",
        "source_url": "https://example.com/team",
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 50,
        "deduplication_key": "person:ada lovelace||example.com/team",
    }
    defaults.update(values)
    return ContactDiscoveryCandidate(**defaults)


@pytest.mark.parametrize("status", list(ContactDiscoveryStatus))
def test_state_supports_statuses_and_timestamps(
    session: Session, status: ContactDiscoveryStatus
) -> None:
    company = create_company(session)
    state = CompanyContactDiscoveryState(company_id=company.id, discovery_status=status)
    session.add(state)
    session.commit()
    assert state.discovery_status == status
    assert state.created_at is not None
    assert state.updated_at is not None


def test_state_company_id_is_unique(session: Session) -> None:
    company = create_company(session)
    session.add_all(
        [
            CompanyContactDiscoveryState(company_id=company.id),
            CompanyContactDiscoveryState(company_id=company.id),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_invalid_state_status_rejected_by_schema_and_database(session: Session) -> None:
    with pytest.raises(ValidationError):
        ContactDiscoveryStateCreate(company_id=1, discovery_status="UNKNOWN")
    company = create_company(session)
    state = CompanyContactDiscoveryState(company_id=company.id)
    state.discovery_status = "UNKNOWN"  # type: ignore[assignment]
    session.add(state)
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("source_type", list(ContactDiscoverySourceType))
def test_candidate_supports_source_types(
    session: Session, source_type: ContactDiscoverySourceType
) -> None:
    company = create_company(session)
    item = candidate(company.id, source_type=source_type)
    session.add(item)
    session.commit()
    assert item.source_type == source_type


@pytest.mark.parametrize("status", list(ContactDiscoveryCandidateStatus))
def test_candidate_supports_statuses(
    session: Session, status: ContactDiscoveryCandidateStatus
) -> None:
    company = create_company(session)
    item = candidate(company.id, discovery_status=status)
    session.add(item)
    session.commit()
    assert item.discovery_status == status


@pytest.mark.parametrize("confidence", [0, 100])
def test_candidate_confidence_accepts_bounds(session: Session, confidence: int) -> None:
    company = create_company(session)
    item = candidate(company.id, confidence=confidence)
    session.add(item)
    session.commit()
    assert item.confidence == confidence
    assert item.created_at is not None
    assert item.updated_at is not None


@pytest.mark.parametrize("confidence", [-1, 101])
def test_candidate_confidence_rejects_out_of_bounds(confidence: int) -> None:
    with pytest.raises(ValidationError):
        ContactDiscoveryCandidateCreate(
            company_id=1,
            email="person@example.com",
            source_type=ContactDiscoverySourceType.CONTACT_PAGE,
            confidence=confidence,
        )


def test_candidate_company_deduplication_key_is_unique(session: Session) -> None:
    company = create_company(session)
    session.add_all([candidate(company.id), candidate(company.id)])
    with pytest.raises(IntegrityError):
        session.commit()


def test_same_deduplication_key_is_allowed_for_different_companies(session: Session) -> None:
    first = create_company(session)
    second = create_company(session)
    session.add_all([candidate(first.id), candidate(second.id)])
    session.commit()


def test_company_delete_cascades_discovery_rows(session: Session) -> None:
    company = create_company(session)
    state = CompanyContactDiscoveryState(company_id=company.id)
    item = candidate(company.id)
    session.add_all([state, item])
    session.commit()
    state_id, candidate_id = state.id, item.id
    session.delete(company)
    session.commit()
    session.expunge_all()
    assert session.get(CompanyContactDiscoveryState, state_id) is None
    assert session.get(ContactDiscoveryCandidate, candidate_id) is None


def test_candidate_schema_rejects_missing_identity_and_raw_markup() -> None:
    with pytest.raises(ValidationError):
        ContactDiscoveryCandidateCreate(
            company_id=1,
            source_type=ContactDiscoverySourceType.OTHER_PUBLIC_PAGE,
        )
    with pytest.raises(ValidationError):
        ContactDiscoveryCandidateCreate(
            company_id=1,
            name="Person",
            source_type=ContactDiscoverySourceType.OTHER_PUBLIC_PAGE,
            notes="<html>raw marker</html>",
        )


def test_candidate_promotion_link_mapping_is_nullable_and_unrelated() -> None:
    column = ContactDiscoveryCandidate.__table__.c.promoted_contact_id
    foreign_key = next(iter(column.foreign_keys))
    assert column.nullable is True
    assert column.default is None
    assert foreign_key.target_fullname == "contacts.id"
    assert foreign_key.ondelete == "SET NULL"
    assert ContactDiscoveryCandidate(company_id=1).promoted_contact_id is None
    assert "promoted_contact" not in ContactDiscoveryCandidate.__mapper__.relationships
    assert [status.value for status in ContactDiscoveryCandidateStatus] == [
        "DISCOVERED",
        "REVIEWED",
        "PROMOTED",
        "REJECTED",
    ]
    constraint_names = {
        constraint.name for constraint in ContactDiscoveryCandidate.__table__.constraints
    }
    assert "uq_contact_discovery_candidates_company_deduplication_key" in constraint_names
    assert "ck_contact_discovery_candidates_status" in constraint_names


def test_candidate_read_supports_optional_promotion_link() -> None:
    values = {
        "id": 1,
        "company_id": 2,
        "name": "Ada",
        "title": None,
        "email": "ada@example.com",
        "normalized_email": "ada@example.com",
        "phone": None,
        "source_url": None,
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 80,
        "discovery_status": ContactDiscoveryCandidateStatus.REVIEWED,
        "deduplication_key": "email:ada@example.com",
        "notes": None,
        "last_error": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    assert ContactDiscoveryCandidateRead(**values).promoted_contact_id is None
    assert ContactDiscoveryCandidateRead(**values, promoted_contact_id=9).promoted_contact_id == 9


def test_candidate_write_schemas_reject_promotion_link() -> None:
    with pytest.raises(ValidationError):
        ContactDiscoveryCandidateCreate(
            company_id=1,
            name="Ada",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
            promoted_contact_id=9,
        )
    with pytest.raises(ValidationError):
        ContactDiscoveryCandidateUpdate(name="Ada", promoted_contact_id=9)

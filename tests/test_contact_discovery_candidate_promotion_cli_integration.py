from collections.abc import Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli.contact_discovery_candidates import execute_promote_candidate
from app.core.database.base import Base
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact_discovery import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoveryRepository,
    ContactDiscoverySourceType,
)
from app.modules.contact_discovery.models import ContactDiscoveryCandidate
from app.modules.project.models import Project


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
    email: str | None = "ADA@example.com",
    status: ContactDiscoveryCandidateStatus = ContactDiscoveryCandidateStatus.REVIEWED,
    suffix: str = "",
) -> ContactDiscoveryCandidate:
    repository = ContactDiscoveryRepository(session)
    result = repository.upsert_candidate(
        company.id,
        ContactDiscoveryCandidateCreate(
            company_id=company.id,
            name="Ada Lovelace",
            title="Chief Scientist",
            email=email,
            phone="+1 555 0100",
            source_url=f"https://example.com/team{suffix}",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
            confidence=90,
        ),
    )
    record = result.candidate
    if status is not ContactDiscoveryCandidateStatus.DISCOVERED:
        repository.set_candidate_status(company.id, record.id, status)
    return session.get(ContactDiscoveryCandidate, record.id)  # type: ignore[return-value]


class TrackingSession(Session):
    commit_calls = 0
    close_calls = 0
    fail_commit = False

    def commit(self) -> None:
        type(self).commit_calls += 1
        if type(self).fail_commit:
            raise RuntimeError("commit failed")
        super().commit()

    def close(self) -> None:
        type(self).close_calls += 1
        super().close()


def tracking_factory(*, fail_commit: bool = False) -> Callable[[], Session]:
    TrackingSession.commit_calls = 0
    TrackingSession.close_calls = 0
    TrackingSession.fail_commit = fail_commit
    bind = SessionLocal.kw["bind"]
    return lambda: TrackingSession(bind=bind)


def test_cli_commits_new_contact_exact_mapping_and_safe_result() -> None:
    with SessionLocal() as setup:
        company = create_company(setup)
        candidate = create_candidate(setup, company)
        company_snapshot = (company.name, company.status, company.notes)
        candidate_snapshot = (
            candidate.name,
            candidate.title,
            candidate.email,
            candidate.phone,
            candidate.source_url,
        )
        setup.commit()
        company_id, candidate_id = company.id, candidate.id

    factory = tracking_factory()
    outcome = execute_promote_candidate(
        company_id=company_id,
        candidate_id=candidate_id,
        yes=True,
        session_factory=factory,
    )
    assert outcome.exit_code == 0
    assert outcome.result is not None
    assert outcome.result.created_contact is True
    assert outcome.result.changed is True
    assert TrackingSession.commit_calls == TrackingSession.close_calls == 1

    with SessionLocal() as verification:
        stored = verification.get(ContactDiscoveryCandidate, candidate_id)
        contact = verification.get(Contact, outcome.result.contact_id)
        company = verification.get(Company, company_id)
        assert stored is not None and contact is not None and company is not None
        persisted_status = ContactDiscoveryCandidateStatus(stored.discovery_status)
        assert persisted_status is ContactDiscoveryCandidateStatus.PROMOTED
        assert stored.promoted_contact_id == contact.id
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
            "Ada",
            "Lovelace",
            "Chief Scientist",
            "ada@example.com",
            "+1 555 0100",
            "CONTACT_DISCOVERY",
            f"contact-discovery-candidate:{candidate_id}",
            "NEW",
        )
        assert company_snapshot == (company.name, company.status, company.notes)
        assert candidate_snapshot == (
            stored.name,
            stored.title,
            stored.email,
            stored.phone,
            stored.source_url,
        )
        for table_name in ("leads", "tasks"):
            assert (
                verification.scalar(
                    select(func.count()).select_from(Base.metadata.tables[table_name])
                )
                == 0
            )


def test_duplicate_reuse_and_repeated_promotion_are_idempotent() -> None:
    with SessionLocal() as setup:
        company = create_company(setup)
        existing = Contact(
            company_id=company.id,
            first_name="Existing",
            email=" ADA@EXAMPLE.COM ",
            phone="preserve",
            source="MANUAL",
            status="ACTIVE",
            notes="private",
        )
        setup.add(existing)
        candidate = create_candidate(setup, company)
        setup.commit()
        snapshot = (
            existing.first_name,
            existing.email,
            existing.phone,
            existing.source,
            existing.status,
            existing.notes,
        )
        company_id, candidate_id, contact_id = company.id, candidate.id, existing.id

    first = execute_promote_candidate(
        company_id=company_id,
        candidate_id=candidate_id,
        yes=True,
    )
    second = execute_promote_candidate(
        company_id=company_id,
        candidate_id=candidate_id,
        yes=True,
    )
    assert first.result is not None and second.result is not None
    assert first.result.contact_id == second.result.contact_id == contact_id
    assert first.result.created_contact is False and first.result.changed is True
    assert second.result.created_contact is False and second.result.changed is False
    with SessionLocal() as verification:
        existing = verification.get(Contact, contact_id)
        assert existing is not None
        assert snapshot == (
            existing.first_name,
            existing.email,
            existing.phone,
            existing.source,
            existing.status,
            existing.notes,
        )
        assert verification.scalar(select(func.count()).select_from(Contact)) == 1


@pytest.mark.parametrize(
    "status",
    [ContactDiscoveryCandidateStatus.DISCOVERED, ContactDiscoveryCandidateStatus.REJECTED],
)
def test_ineligible_candidates_create_nothing(status: ContactDiscoveryCandidateStatus) -> None:
    with SessionLocal() as setup:
        company = create_company(setup)
        candidate = create_candidate(setup, company, status=status)
        setup.commit()
        company_id, candidate_id = company.id, candidate.id
    outcome = execute_promote_candidate(company_id=company_id, candidate_id=candidate_id, yes=True)
    assert outcome.error_message == "Candidate is not eligible for promotion."
    with SessionLocal() as verification:
        stored = verification.get(ContactDiscoveryCandidate, candidate_id)
        assert stored is not None and stored.promoted_contact_id is None
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0


def test_cross_company_and_inconsistent_link_are_sanitized_without_creation() -> None:
    with SessionLocal() as setup:
        first = create_company(setup, "First")
        second = create_company(setup, "Second")
        candidate = create_candidate(setup, second)
        linked = Contact(company_id=second.id, first_name="Linked")
        setup.add(linked)
        setup.flush()
        candidate.promoted_contact_id = linked.id
        setup.commit()
        first_id, second_id, candidate_id = first.id, second.id, candidate.id

    missing = execute_promote_candidate(company_id=first_id, candidate_id=candidate_id, yes=True)
    inconsistent = execute_promote_candidate(
        company_id=second_id, candidate_id=candidate_id, yes=True
    )
    assert missing.error_message == "Candidate was not found."
    assert inconsistent.error_message == "Candidate promotion state is inconsistent."
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Contact)) == 1


def test_commit_failure_rolls_back_flushed_contact_and_link() -> None:
    with SessionLocal() as setup:
        company = create_company(setup)
        candidate = create_candidate(setup, company, email="rollback@example.com")
        setup.commit()
        company_id, candidate_id = company.id, candidate.id
    factory = tracking_factory(fail_commit=True)
    outcome = execute_promote_candidate(
        company_id=company_id,
        candidate_id=candidate_id,
        yes=True,
        session_factory=factory,
    )
    assert outcome.error_message == "Candidate promotion failed."
    assert TrackingSession.commit_calls == TrackingSession.close_calls == 1
    with SessionLocal() as verification:
        stored = verification.get(ContactDiscoveryCandidate, candidate_id)
        assert stored is not None
        persisted_status = ContactDiscoveryCandidateStatus(stored.discovery_status)
        assert persisted_status is ContactDiscoveryCandidateStatus.REVIEWED
        assert stored.promoted_contact_id is None
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0

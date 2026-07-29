import socket
import urllib.request
from collections.abc import Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.lead import ContactLeadCreationCommandOutcome, execute_create_lead_from_contact
from app.cli.main import app as root_app
from app.core.database.engine import engine
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.lead.models import Lead
from app.modules.project.repository import ProjectRepository
from app.modules.task.models import Task

runner = CliRunner()


class TrackingSession(Session):
    def __init__(self, *, fail_commit: bool = False) -> None:
        super().__init__(bind=engine, expire_on_commit=False)
        self.fail_commit = fail_commit
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        if self.fail_commit:
            raise RuntimeError("private commit failure")
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def tracking_factory(
    *,
    fail_commit: bool = False,
) -> tuple[Callable[[], Session], list[TrackingSession]]:
    sessions: list[TrackingSession] = []

    def factory() -> Session:
        session = TrackingSession(fail_commit=fail_commit)
        sessions.append(session)
        return session

    return factory, sessions


def seed_company_contact(
    *,
    company_name: str = "CLI Company",
) -> tuple[int, int]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("CLI Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name=company_name,
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
            email="private@example.com",
            notes="private notes",
        )
        session.commit()
        return company.id, contact.id


def contact_state(contact: Contact) -> tuple[object, ...]:
    return (
        contact.company_id,
        contact.first_name,
        contact.last_name,
        contact.email,
        contact.phone,
        contact.source,
        contact.status,
        contact.notes,
    )


def company_state(company: Company) -> tuple[object, ...]:
    return (
        company.project_id,
        company.name,
        company.website,
        company.status,
        company.notes,
    )


def test_confirmed_success_commits_closes_and_has_no_domain_side_effects() -> None:
    company_id, contact_id = seed_company_contact()
    with SessionLocal() as before:
        company = before.get(Company, company_id)
        contact = before.get(Contact, contact_id)
        assert company is not None and contact is not None
        expected_company = company_state(company)
        expected_contact = contact_state(contact)

    factory, sessions = tracking_factory()
    outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=True,
        session_factory=factory,
    )

    assert outcome.exit_code == 0
    assert outcome.error_message is None
    assert outcome.result is not None
    assert len(sessions) == 1
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        1,
        0,
        1,
    )
    with SessionLocal() as verification:
        leads = verification.scalars(select(Lead)).all()
        assert len(leads) == 1
        lead = leads[0]
        assert lead.id == outcome.result.lead_id
        assert (lead.company_id, lead.contact_id, lead.status) == (
            company_id,
            contact_id,
            "NEW",
        )
        assert lead.source is None and lead.notes is None
        company = verification.get(Company, company_id)
        contact = verification.get(Contact, contact_id)
        assert company is not None and contact is not None
        assert company_state(company) == expected_company
        assert contact_state(contact) == expected_contact
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_missing_confirmation_constructs_no_session_or_lead() -> None:
    company_id, contact_id = seed_company_contact()
    calls: list[str] = []
    outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=False,
        session_factory=lambda: calls.append("session"),  # type: ignore[arg-type]
    )
    assert outcome == ContactLeadCreationCommandOutcome(
        exit_code=1,
        error_message="Lead creation requires --yes.",
    )
    assert calls == []
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0


def test_contact_not_found_rolls_back_closes_and_persists_nothing() -> None:
    company_id, _ = seed_company_contact()
    factory, sessions = tracking_factory()
    outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=2_147_483_647,
        yes=True,
        session_factory=factory,
    )
    assert outcome.error_message == "Contact was not found."
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        0,
        1,
        1,
    )
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_missing_company_hides_existing_contact_and_preserves_it() -> None:
    _, contact_id = seed_company_contact()
    with SessionLocal() as before:
        contact = before.get(Contact, contact_id)
        assert contact is not None
        expected = contact_state(contact)
    outcome = execute_create_lead_from_contact(
        company_id=2_147_483_647,
        contact_id=contact_id,
        yes=True,
    )
    assert outcome.error_message == "Contact was not found."
    with SessionLocal() as verification:
        contact = verification.get(Contact, contact_id)
        assert contact is not None
        assert contact_state(contact) == expected
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0


def test_cross_company_contact_is_hidden_without_mutation() -> None:
    first_id, contact_id = seed_company_contact(company_name="First")
    second_id, _ = seed_company_contact(company_name="Second")
    with SessionLocal() as before:
        first = before.get(Company, first_id)
        second = before.get(Company, second_id)
        contact = before.get(Contact, contact_id)
        assert first is not None and second is not None and contact is not None
        states = company_state(first), company_state(second), contact_state(contact)
    outcome = execute_create_lead_from_contact(
        company_id=second_id,
        contact_id=contact_id,
        yes=True,
    )
    assert outcome.error_message == "Contact was not found."
    with SessionLocal() as verification:
        first = verification.get(Company, first_id)
        second = verification.get(Company, second_id)
        contact = verification.get(Contact, contact_id)
        assert first is not None and second is not None and contact is not None
        assert (company_state(first), company_state(second), contact_state(contact)) == states
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0


def test_repeated_confirmed_execution_intentionally_creates_two_leads() -> None:
    company_id, contact_id = seed_company_contact()
    first = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=True,
    )
    second = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=True,
    )
    assert first.result is not None and second.result is not None
    assert first.result.lead_id != second.result.lead_id
    with SessionLocal() as verification:
        leads = verification.scalars(select(Lead).order_by(Lead.id)).all()
        assert [lead.id for lead in leads] == [
            first.result.lead_id,
            second.result.lead_id,
        ]
        assert all(
            lead.status == "NEW" and lead.source is None and lead.notes is None for lead in leads
        )


def test_commit_failure_rolls_back_real_flushed_lead() -> None:
    company_id, contact_id = seed_company_contact()
    factory, sessions = tracking_factory(fail_commit=True)
    outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=True,
        session_factory=factory,
    )
    assert outcome.error_message == "Lead creation failed."
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        1,
        1,
        1,
    )
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.get(Company, company_id) is not None
        assert verification.get(Contact, contact_id) is not None


def test_confirmed_execution_does_not_use_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id, contact_id = seed_company_contact()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network operation attempted")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=True,
    )
    assert outcome.exit_code == 0


def test_legacy_generic_cli_remains_functional() -> None:
    company_id, _ = seed_company_contact()
    result = runner.invoke(root_app, ["lead", "create", str(company_id)])
    assert result.exit_code == 0
    assert "Lead created" in result.output
    with SessionLocal() as verification:
        lead = verification.scalar(select(Lead))
        assert lead is not None
        assert lead.company_id == company_id
        assert lead.contact_id is None

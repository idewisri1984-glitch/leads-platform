import socket
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.lead.contact_lead_creation import (
    ContactLeadCreationContactRecord,
    ContactLeadCreationContactRepository,
    ContactLeadCreationNotFoundError,
    ContactLeadCreationService,
)
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.lead.schemas import LeadCreate
from app.modules.lead.service import LeadService
from app.modules.project.repository import ProjectRepository


@contextmanager
def seeded_session(
    *,
    second_company: bool = False,
) -> Iterator[tuple[Session, Company, Contact, Company | None]]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Contact Lead Service Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Contact Lead Service Company",
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
            email="private@example.com",
            notes="private contact notes",
        )
        other_company = None
        if second_company:
            other_company = CompanyRepository(session).create(
                project_id=project.id,
                name="Other Contact Lead Company",
            )
        yield session, company, contact, other_company


def non_lead_table_counts(session: Session) -> dict[str, int]:
    table_names = inspect(session.get_bind()).get_table_names()
    return {
        table_name: session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
        for table_name in table_names
        if table_name != Lead.__tablename__
    }


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


def service_for(session: Session) -> ContactLeadCreationService:
    return ContactLeadCreationService(
        ContactRepository(session),
        LeadRepository(session),
    )


def forbid_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Contact Lead creation attempted a network operation.")


def install_network_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)


def test_caller_rollback_removes_flushed_lead_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_network_guards(monkeypatch)
    with seeded_session() as (session, company, contact, _):
        before_counts = non_lead_table_counts(session)
        before_company = company_state(company)
        before_contact = contact_state(contact)

        result = service_for(session).create(company.id, contact.id)
        stored = session.get(Lead, result.lead_id)

        assert type(result.lead_id) is int and result.lead_id > 0
        assert stored is not None
        assert stored.id == result.lead_id
        assert non_lead_table_counts(session) == before_counts
        assert company_state(company) == before_company
        assert contact_state(contact) == before_contact
        lead_id = result.lead_id
        company_id = company.id
        contact_id = contact.id
        session.rollback()

    with SessionLocal() as verification:
        assert verification.get(Lead, lead_id) is None
        assert verification.get(Company, company_id) is not None
        assert verification.get(Contact, contact_id) is not None
        assert non_lead_table_counts(verification) == before_counts


def test_caller_commit_persists_exact_lead_without_side_effects() -> None:
    with seeded_session() as (session, company, contact, _):
        before_counts = non_lead_table_counts(session)
        before_company = company_state(company)
        before_contact = contact_state(contact)

        result = service_for(session).create(company.id, contact.id)
        session.commit()
        lead_id = result.lead_id

    with SessionLocal() as verification:
        leads = verification.scalars(select(Lead)).all()
        assert len(leads) == 1
        stored = leads[0]
        assert stored.id == lead_id
        assert stored.company_id == company.id
        assert stored.contact_id == contact.id
        assert stored.status == "NEW"
        assert stored.source is None
        assert stored.notes is None
        stored_company = verification.get(Company, company.id)
        stored_contact = verification.get(Contact, contact.id)
        assert stored_company is not None
        assert stored_contact is not None
        assert company_state(stored_company) == before_company
        assert contact_state(stored_contact) == before_contact
        assert non_lead_table_counts(verification) == before_counts


def test_nonexistent_contact_is_hidden_as_not_found() -> None:
    with seeded_session() as (session, company, _contact, _):
        with pytest.raises(
            ContactLeadCreationNotFoundError,
            match=r"^Contact was not found\.$",
        ):
            service_for(session).create(company.id, 2_147_483_647)

        assert session.scalars(select(Lead)).all() == []
        session.rollback()


def test_nonexistent_company_hides_contact_and_creates_no_lead() -> None:
    with seeded_session() as (session, _company, contact, _):
        before_contact = contact_state(contact)

        with pytest.raises(
            ContactLeadCreationNotFoundError,
            match=r"^Contact was not found\.$",
        ):
            service_for(session).create(2_147_483_647, contact.id)

        assert session.scalars(select(Lead)).all() == []
        assert contact_state(contact) == before_contact
        session.rollback()


def test_cross_company_contact_is_hidden_and_neither_company_is_mutated() -> None:
    with seeded_session(second_company=True) as (session, company, contact, other):
        assert other is not None
        before_company = company_state(company)
        before_other = company_state(other)
        before_contact = contact_state(contact)

        with pytest.raises(
            ContactLeadCreationNotFoundError,
            match=r"^Contact was not found\.$",
        ):
            service_for(session).create(other.id, contact.id)

        assert session.scalars(select(Lead)).all() == []
        assert company_state(company) == before_company
        assert company_state(other) == before_other
        assert contact_state(contact) == before_contact
        session.rollback()


def test_repeated_success_is_non_idempotent_and_persists_two_leads() -> None:
    with seeded_session() as (session, company, contact, _):
        service = service_for(session)

        first = service.create(company.id, contact.id)
        second = service.create(company.id, contact.id)

        assert first.lead_id != second.lead_id
        assert first.lead_id > 0
        assert second.lead_id > 0
        assert len(session.scalars(select(Lead)).all()) == 2
        session.commit()

    with SessionLocal() as verification:
        stored = verification.scalars(select(Lead).order_by(Lead.id)).all()
        assert [lead.id for lead in stored] == [first.lead_id, second.lead_id]


class MissingContactLookup:
    def __init__(self, company_id: int, contact_id: int) -> None:
        self.company_id = company_id
        self.contact_id = contact_id

    def get_for_company(
        self,
        company_id: int,
        contact_id: int,
    ) -> ContactLeadCreationContactRecord:
        assert company_id == self.company_id
        assert contact_id == self.contact_id
        return cast(
            ContactLeadCreationContactRecord,
            type(
                "MissingContactRecord",
                (),
                {"id": self.contact_id, "company_id": self.company_id},
            )(),
        )


def test_structural_race_integrity_error_propagates_and_caller_rolls_back() -> None:
    with seeded_session() as (session, company, _contact, _):
        missing_contact_id = 2_147_483_647
        service = ContactLeadCreationService(
            cast(
                ContactLeadCreationContactRepository,
                MissingContactLookup(company.id, missing_contact_id),
            ),
            LeadRepository(session),
        )

        with pytest.raises(IntegrityError):
            service.create(company.id, missing_contact_id)

        session.rollback()

    with SessionLocal() as verification:
        assert verification.scalars(select(Lead)).all() == []


def test_legacy_lead_service_path_remains_functional() -> None:
    with seeded_session() as (session, company, contact, _):
        legacy_service = LeadService(
            LeadRepository(session),
            ContactRepository(session),
        )

        result = legacy_service.create(
            LeadCreate(
                company_id=company.id,
                contact_id=contact.id,
                source="legacy",
                notes="legacy notes",
            )
        )

        assert result.id > 0
        assert result.source == "legacy"
        assert result.notes == "legacy notes"

import socket
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository


@contextmanager
def seeded_session() -> Iterator[tuple[Session, Company, Contact]]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Lead Creation Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Lead Creation Company",
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
        )
        yield session, company, contact


def non_lead_table_counts(session: Session) -> dict[str, int]:
    bind = session.get_bind()
    table_names = inspect(bind).get_table_names()
    return {
        table_name: session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
        for table_name in table_names
        if table_name != Lead.__tablename__
    }


def forbid_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Lead repository attempted a network operation.")


def install_network_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)


def test_create_for_contact_is_visible_after_flush_and_caller_can_rollback() -> None:
    with seeded_session() as (session, company, contact):
        before = non_lead_table_counts(session)
        lead = LeadRepository(session).create_for_contact(
            company_id=company.id,
            contact_id=contact.id,
            status=" Qualified ",
            source=" Manual  Entry ",
        )

        assert lead.id is not None
        assert session.get(Lead, lead.id) is lead
        assert non_lead_table_counts(session) == before
        lead_id = lead.id
        company_id = company.id
        contact_id = contact.id
        session.rollback()

    with SessionLocal() as verification:
        assert verification.get(Lead, lead_id) is None
        assert verification.get(Company, company_id) is not None
        assert verification.get(Contact, contact_id) is not None
        assert non_lead_table_counts(verification) == before


def test_caller_commit_persists_exact_lead_without_other_mutations() -> None:
    with seeded_session() as (session, company, contact):
        before = non_lead_table_counts(session)
        lead = LeadRepository(session).create_for_contact(
            company_id=company.id,
            contact_id=contact.id,
            status="MiXeD",
            source=" Internal  Referral ",
        )
        lead_id = lead.id
        session.commit()

    with SessionLocal() as verification:
        stored = verification.get(Lead, lead_id)
        assert stored is not None
        assert stored.company_id == company.id
        assert stored.contact_id == contact.id
        assert stored.status == "MiXeD"
        assert stored.source == " Internal  Referral "
        assert stored.notes is None
        assert non_lead_table_counts(verification) == before


def test_invalid_company_foreign_key_propagates_and_caller_rolls_back() -> None:
    with seeded_session() as (session, _company, contact):
        before = non_lead_table_counts(session)
        contact_id = contact.id

        with pytest.raises(IntegrityError):
            LeadRepository(session).create_for_contact(
                company_id=2_147_483_647,
                contact_id=contact.id,
            )

        session.rollback()

    with SessionLocal() as verification:
        assert verification.scalars(select(Lead)).all() == []
        assert verification.get(Contact, contact_id) is not None
        assert non_lead_table_counts(verification) == before


def test_invalid_contact_foreign_key_propagates_and_caller_rolls_back() -> None:
    with seeded_session() as (session, company, _contact):
        before = non_lead_table_counts(session)
        company_id = company.id

        with pytest.raises(IntegrityError):
            LeadRepository(session).create_for_contact(
                company_id=company.id,
                contact_id=2_147_483_647,
            )

        session.rollback()

    with SessionLocal() as verification:
        assert verification.scalars(select(Lead)).all() == []
        assert verification.get(Company, company_id) is not None
        assert non_lead_table_counts(verification) == before


@pytest.mark.parametrize(
    "call",
    [
        lambda repository, company_id, contact_id: repository.create_for_contact(
            company_id=0,
            contact_id=contact_id,
        ),
        lambda repository, company_id, contact_id: repository.create_for_contact(
            company_id=company_id,
            contact_id=True,
        ),
        lambda repository, company_id, contact_id: repository.create_for_contact(
            company_id=company_id,
            contact_id=contact_id,
            status=" ",
        ),
        lambda repository, company_id, contact_id: repository.create_for_contact(
            company_id=company_id,
            contact_id=contact_id,
            source=" ",
        ),
    ],
)
def test_primitive_invalid_data_fails_before_mutation(
    call: Callable[[LeadRepository, int, int], Lead],
) -> None:
    with seeded_session() as (session, company, contact):
        before = non_lead_table_counts(session)

        with pytest.raises(ValueError, match=r"^Lead creation data is invalid\.$"):
            call(LeadRepository(session), company.id, contact.id)

        assert not session.new
        assert not session.dirty
        assert not session.deleted
        assert session.scalars(select(Lead)).all() == []
        assert non_lead_table_counts(session) == before


def test_two_calls_create_distinct_leads_and_rollback_removes_both() -> None:
    with seeded_session() as (session, company, contact):
        repository = LeadRepository(session)
        first = repository.create_for_contact(
            company_id=company.id,
            contact_id=contact.id,
        )
        second = repository.create_for_contact(
            company_id=company.id,
            contact_id=contact.id,
        )

        assert first is not second
        assert first.id != second.id
        assert len(session.scalars(select(Lead)).all()) == 2
        session.rollback()

    with SessionLocal() as verification:
        assert verification.scalars(select(Lead)).all() == []


def test_creation_has_no_network_or_non_lead_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_network_guards(monkeypatch)

    with seeded_session() as (session, company, contact):
        before = non_lead_table_counts(session)
        company_state: tuple[Any, ...] = (
            company.name,
            company.status,
            company.notes,
        )
        contact_state: tuple[Any, ...] = (
            contact.first_name,
            contact.last_name,
            contact.status,
            contact.notes,
        )

        lead = LeadRepository(session).create_for_contact(
            company_id=company.id,
            contact_id=contact.id,
        )

        assert lead.id is not None
        assert non_lead_table_counts(session) == before
        assert (company.name, company.status, company.notes) == company_state
        assert (
            contact.first_name,
            contact.last_name,
            contact.status,
            contact.notes,
        ) == contact_state
        session.rollback()

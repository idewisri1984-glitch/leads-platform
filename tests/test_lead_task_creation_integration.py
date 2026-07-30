import socket
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.lead.contact_lead_creation import ContactLeadCreationService
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.models import Project
from app.modules.project.repository import ProjectRepository
from app.modules.task import LeadTaskCreationNotFoundError, LeadTaskCreationService
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository


@contextmanager
def seeded_session(
    *,
    second_company: bool = False,
) -> Iterator[tuple[Session, Project, Company, Contact, Lead, Company | None]]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Lead Task Service Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Lead Task Service Company",
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
            email="private@example.com",
        )
        lead = LeadRepository(session).create(
            company_id=company.id,
            contact_id=contact.id,
        )
        other = None
        if second_company:
            other = CompanyRepository(session).create(
                project_id=project.id,
                name="Other Lead Task Company",
            )
        yield session, project, company, contact, lead, other


def service_for(session: Session) -> LeadTaskCreationService:
    return LeadTaskCreationService(
        LeadRepository(session),
        TaskRepository(session),
    )


def task_count(session: Session) -> int:
    return len(session.scalars(select(Task)).all())


def non_task_table_counts(session: Session) -> dict[str, int]:
    table_names = inspect(session.get_bind()).get_table_names()
    return {
        table_name: session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
        for table_name in table_names
        if table_name != Task.__tablename__
    }


def forbid_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Lead Task creation attempted a network operation.")


def test_caller_rollback_removes_flushed_task_and_preserves_lead() -> None:
    with seeded_session() as (session, _project, company, _contact, lead, _other):
        before = non_task_table_counts(session)
        result = service_for(session).create(company.id, lead.id, "Follow up")
        stored = session.get(Task, result.task_id)

        assert stored is not None
        assert stored.id == result.task_id
        assert stored.status == "TODO"
        assert stored.due_at is None
        task_id = result.task_id
        lead_id = lead.id
        session.rollback()

    with SessionLocal() as verification:
        assert verification.get(Task, task_id) is None
        assert verification.get(Lead, lead_id) is not None
        assert non_task_table_counts(verification) == before


def test_caller_commit_persists_exact_task_text_and_fixed_state() -> None:
    with seeded_session() as (session, _project, company, _contact, lead, _other):
        title = "  Follow up  "
        description = "  Call tomorrow  "
        result = service_for(session).create(
            company.id,
            lead.id,
            title,
            description,
        )
        task_id = result.task_id
        lead_id = lead.id
        session.commit()

    with SessionLocal() as verification:
        task = verification.get(Task, task_id)
        assert task is not None
        assert task.lead_id == lead_id
        assert task.title == title
        assert task.description == description
        assert task.status == "TODO"
        assert task.due_at is None


def test_empty_description_persists_as_empty_string() -> None:
    with seeded_session() as (session, _project, company, _contact, lead, _other):
        result = service_for(session).create(company.id, lead.id, "Follow up", "")
        task_id = result.task_id
        session.commit()

    with SessionLocal() as verification:
        task = verification.get(Task, task_id)
        assert task is not None
        assert task.description == ""


@pytest.mark.parametrize("missing", ["company", "lead"])
def test_missing_scope_is_hidden_as_not_found(missing: str) -> None:
    with seeded_session() as (session, _project, company, _contact, lead, _other):
        company_id = 2_147_483_647 if missing == "company" else company.id
        lead_id = 2_147_483_647 if missing == "lead" else lead.id

        with pytest.raises(
            LeadTaskCreationNotFoundError,
            match=r"^Lead was not found\.$",
        ):
            service_for(session).create(company_id, lead_id, "Follow up")

        assert task_count(session) == 0


def test_cross_company_lead_is_hidden_as_not_found() -> None:
    with seeded_session(second_company=True) as (
        session,
        _project,
        _company,
        _contact,
        lead,
        other,
    ):
        assert other is not None

        with pytest.raises(
            LeadTaskCreationNotFoundError,
            match=r"^Lead was not found\.$",
        ):
            service_for(session).create(other.id, lead.id, "Follow up")

        assert task_count(session) == 0


def test_repeated_creation_can_be_rolled_back_and_committed() -> None:
    with seeded_session() as (session, _project, company, _contact, lead, _other):
        service = service_for(session)
        first = service.create(company.id, lead.id, "Follow up")
        second = service.create(company.id, lead.id, "Follow up")

        assert first.task_id != second.task_id
        assert task_count(session) == 2
        session.rollback()

    with SessionLocal() as verification:
        assert task_count(verification) == 0

    with seeded_session() as (session, _project, company, _contact, lead, _other):
        service = service_for(session)
        first = service.create(company.id, lead.id, "Follow up")
        second = service.create(company.id, lead.id, "Follow up")
        expected_ids = [first.task_id, second.task_id]
        session.commit()

    with SessionLocal() as verification:
        assert [task.id for task in verification.scalars(select(Task).order_by(Task.id))] == (
            expected_ids
        )


def test_creation_has_no_network_or_other_domain_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)

    with seeded_session() as (session, project, company, contact, lead, _other):
        before_counts = non_task_table_counts(session)
        project_state: tuple[Any, ...] = (project.name,)
        company_state: tuple[Any, ...] = (
            company.project_id,
            company.name,
            company.status,
            company.notes,
        )
        contact_state: tuple[Any, ...] = (
            contact.company_id,
            contact.first_name,
            contact.email,
            contact.status,
            contact.notes,
        )
        lead_state: tuple[Any, ...] = (
            lead.company_id,
            lead.contact_id,
            lead.status,
            lead.source,
            lead.notes,
        )

        service_for(session).create(company.id, lead.id, "Follow up")
        session.commit()

    with SessionLocal() as verification:
        stored_project = verification.get(Project, project.id)
        stored_company = verification.get(Company, company.id)
        stored_contact = verification.get(Contact, contact.id)
        stored_lead = verification.get(Lead, lead.id)
        assert stored_project is not None
        assert stored_company is not None
        assert stored_contact is not None
        assert stored_lead is not None
        assert (stored_project.name,) == project_state
        assert (
            stored_company.project_id,
            stored_company.name,
            stored_company.status,
            stored_company.notes,
        ) == company_state
        assert (
            stored_contact.company_id,
            stored_contact.first_name,
            stored_contact.email,
            stored_contact.status,
            stored_contact.notes,
        ) == contact_state
        assert (
            stored_lead.company_id,
            stored_lead.contact_id,
            stored_lead.status,
            stored_lead.source,
            stored_lead.notes,
        ) == lead_state
        assert non_task_table_counts(verification) == before_counts


def test_contact_scoped_lead_creation_still_creates_no_task() -> None:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Independent Lead Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Independent Lead Company",
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Grace",
        )
        result = ContactLeadCreationService(
            ContactRepository(session),
            LeadRepository(session),
        ).create(company.id, contact.id)

        assert session.get(Lead, result.lead_id) is not None
        assert task_count(session) == 0
        session.rollback()

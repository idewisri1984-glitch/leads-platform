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
from app.modules.project.models import Project
from app.modules.project.repository import ProjectRepository
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository


@contextmanager
def seeded_session() -> Iterator[tuple[Session, Project, Company, Contact, Lead]]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Task Creation Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Task Creation Company",
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
        )
        lead = LeadRepository(session).create(
            company_id=company.id,
            contact_id=contact.id,
        )
        yield session, project, company, contact, lead


def non_task_table_counts(session: Session) -> dict[str, int]:
    bind = session.get_bind()
    table_names = inspect(bind).get_table_names()
    return {
        table_name: session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
        for table_name in table_names
        if table_name != Task.__tablename__
    }


def task_count(session: Session) -> int:
    return len(session.scalars(select(Task)).all())


def forbid_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Task repository attempted a network operation.")


def install_network_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)


def test_create_for_lead_is_visible_after_flush_and_caller_can_rollback() -> None:
    with seeded_session() as (session, _project, _company, _contact, lead):
        before = non_task_table_counts(session)
        task = TaskRepository(session).create_for_lead(
            lead_id=lead.id,
            title="Follow up",
        )

        assert task.id is not None
        assert session.get(Task, task.id) is task
        assert task.status == "TODO"
        assert task.due_at is None
        assert non_task_table_counts(session) == before
        task_id = task.id
        lead_id = lead.id
        session.rollback()

    with SessionLocal() as verification:
        assert verification.get(Task, task_id) is None
        assert verification.get(Lead, lead_id) is not None
        assert non_task_table_counts(verification) == before


def test_caller_commit_persists_exact_task_without_other_mutations() -> None:
    with seeded_session() as (session, _project, _company, _contact, lead):
        before = non_task_table_counts(session)
        lead_id = lead.id
        task = TaskRepository(session).create_for_lead(
            lead_id=lead_id,
            title="  Follow up  ",
            description="  Call tomorrow  ",
        )
        task_id = task.id
        session.commit()

    with SessionLocal() as verification:
        stored = verification.get(Task, task_id)
        assert stored is not None
        assert stored.lead_id == lead_id
        assert stored.title == "  Follow up  "
        assert stored.description == "  Call tomorrow  "
        assert stored.status == "TODO"
        assert stored.due_at is None
        assert non_task_table_counts(verification) == before


def test_empty_description_persists_as_empty_string() -> None:
    with seeded_session() as (session, _project, _company, _contact, lead):
        task = TaskRepository(session).create_for_lead(
            lead_id=lead.id,
            title="Follow up",
            description="",
        )
        task_id = task.id
        session.commit()

    with SessionLocal() as verification:
        stored = verification.get(Task, task_id)
        assert stored is not None
        assert stored.description == ""


def test_missing_lead_foreign_key_error_propagates_and_caller_rolls_back() -> None:
    with seeded_session() as (session, _project, _company, _contact, _lead):
        before = non_task_table_counts(session)

        with pytest.raises(IntegrityError):
            TaskRepository(session).create_for_lead(
                lead_id=2_147_483_647,
                title="Follow up",
            )

        session.rollback()

    with SessionLocal() as verification:
        assert task_count(verification) == 0
        assert non_task_table_counts(verification) == before


@pytest.mark.parametrize(
    "call",
    [
        lambda repository: repository.create_for_lead(lead_id=0, title="Follow up"),
        lambda repository: repository.create_for_lead(lead_id=1, title=" "),
        lambda repository: repository.create_for_lead(
            lead_id=1,
            title="Follow up",
            description=object(),  # type: ignore[arg-type]
        ),
    ],
)
def test_invalid_input_fails_before_real_session_mutation(
    call: Callable[[TaskRepository], Task],
) -> None:
    with seeded_session() as (session, _project, _company, _contact, _lead):
        before_tables = non_task_table_counts(session)
        before_tasks = task_count(session)
        before_new = set(session.new)
        before_dirty = set(session.dirty)
        before_deleted = set(session.deleted)

        with pytest.raises(ValueError, match=r"^Task creation data is invalid\.$"):
            call(TaskRepository(session))

        assert set(session.new) == before_new
        assert set(session.dirty) == before_dirty
        assert set(session.deleted) == before_deleted
        assert task_count(session) == before_tasks
        assert non_task_table_counts(session) == before_tables


def test_repeated_creation_can_be_rolled_back_together() -> None:
    with seeded_session() as (session, _project, _company, _contact, lead):
        repository = TaskRepository(session)
        first = repository.create_for_lead(
            lead_id=lead.id,
            title="Follow up",
            description="Call",
        )
        second = repository.create_for_lead(
            lead_id=lead.id,
            title="Follow up",
            description="Call",
        )

        assert first is not second
        assert first.id != second.id
        assert task_count(session) == 2
        session.rollback()

    with SessionLocal() as verification:
        assert task_count(verification) == 0


def test_repeated_creation_can_be_committed_as_distinct_rows() -> None:
    with seeded_session() as (session, _project, _company, _contact, lead):
        repository = TaskRepository(session)
        first = repository.create_for_lead(lead_id=lead.id, title="Follow up")
        second = repository.create_for_lead(lead_id=lead.id, title="Follow up")
        task_ids = {first.id, second.id}
        session.commit()

    with SessionLocal() as verification:
        stored = verification.scalars(select(Task).order_by(Task.id)).all()
        assert {task.id for task in stored} == task_ids
        assert len(stored) == 2


def test_creation_has_no_network_or_other_domain_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_network_guards(monkeypatch)

    with seeded_session() as (session, project, company, contact, lead):
        before = non_task_table_counts(session)
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
            contact.last_name,
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

        task = TaskRepository(session).create_for_lead(
            lead_id=lead.id,
            title="Follow up",
        )

        assert task.id is not None
        assert non_task_table_counts(session) == before
        assert (project.name,) == project_state
        assert (
            company.project_id,
            company.name,
            company.status,
            company.notes,
        ) == company_state
        assert (
            contact.company_id,
            contact.first_name,
            contact.last_name,
            contact.status,
            contact.notes,
        ) == contact_state
        assert (
            lead.company_id,
            lead.contact_id,
            lead.status,
            lead.source,
            lead.notes,
        ) == lead_state
        task_id = task.id
        session.commit()

    with SessionLocal() as verification:
        assert verification.get(Task, task_id) is not None
        assert non_task_table_counts(verification) == before

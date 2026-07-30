import socket
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
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
from app.modules.task.models import Task, TaskLifecycleStatus
from app.modules.task.repository import (
    TaskLifecycleRepositoryNotFoundError,
    TaskLifecycleRepositoryTransitionError,
    TaskRepository,
)

NOT_FOUND = "Task was not found."
NOT_ALLOWED = "Task status transition is not allowed."


@contextmanager
def seeded_task(
    *,
    status: str = "TODO",
    due_at: datetime | None = None,
) -> Iterator[tuple[Session, Project, Company, Contact, Lead, Task]]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Lifecycle Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Lifecycle Company",
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
        )
        lead = LeadRepository(session).create(
            company_id=company.id,
            contact_id=contact.id,
        )
        task = TaskRepository(session).create(
            lead_id=lead.id,
            title="Lifecycle task",
            description="Preserve this",
            status=status,
            due_at=due_at,
        )
        yield session, project, company, contact, lead, task


def task_status(task_id: int) -> str:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        return task.status


def row_counts(session: Session) -> dict[str, int]:
    return {
        "projects": session.scalar(select(func.count()).select_from(Project)) or 0,
        "companies": session.scalar(select(func.count()).select_from(Company)) or 0,
        "contacts": session.scalar(select(func.count()).select_from(Contact)) or 0,
        "leads": session.scalar(select(func.count()).select_from(Lead)) or 0,
        "tasks": session.scalar(select(func.count()).select_from(Task)) or 0,
    }


def domain_snapshot(
    project: Project,
    company: Company,
    contact: Contact,
    lead: Lead,
) -> tuple[tuple[Any, ...], ...]:
    return (
        (project.id, project.name),
        (company.id, company.project_id, company.name, company.status, company.notes),
        (
            contact.id,
            contact.company_id,
            contact.first_name,
            contact.last_name,
            contact.status,
            contact.notes,
        ),
        (
            lead.id,
            lead.company_id,
            lead.contact_id,
            lead.status,
            lead.source,
            lead.notes,
        ),
    )


def forbid_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Lifecycle persistence attempted a network operation.")


def install_network_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)


def test_caller_rollback_discards_flushed_transition() -> None:
    with seeded_task() as (session, _project, company, _contact, _lead, task):
        task_id = task.id
        result = TaskRepository(session).set_status_for_company(
            company.id,
            task.id,
            TaskLifecycleStatus.IN_PROGRESS,
        )
        assert result.changed is True
        assert result.task.status == "IN_PROGRESS"
        session.rollback()
    assert task_status(task_id) == "TODO"


def test_caller_commit_persists_transition() -> None:
    with seeded_task() as (session, _project, company, _contact, _lead, task):
        task_id = task.id
        TaskRepository(session).set_status_for_company(
            company.id,
            task.id,
            TaskLifecycleStatus.CANCELLED,
        )
        session.commit()
    assert task_status(task_id) == "CANCELLED"


def test_second_changed_transition_reaches_done() -> None:
    with seeded_task() as (session, _project, company, _contact, _lead, task):
        task_id = task.id
        repository = TaskRepository(session)
        repository.set_status_for_company(
            company.id,
            task.id,
            TaskLifecycleStatus.IN_PROGRESS,
        )
        session.commit()
        result = repository.set_status_for_company(
            company.id,
            task.id,
            TaskLifecycleStatus.DONE,
        )
        assert result.previous_status is TaskLifecycleStatus.IN_PROGRESS
        session.commit()
    assert task_status(task_id) == "DONE"


@pytest.mark.parametrize("status", list(TaskLifecycleStatus))
def test_persisted_idempotent_transition_changes_nothing(
    status: TaskLifecycleStatus,
) -> None:
    due_at = datetime(2027, 1, 2, 3, 4)
    with seeded_task(status=status.value, due_at=due_at) as (
        session,
        _project,
        company,
        _contact,
        lead,
        task,
    ):
        before = (task.lead_id, task.title, task.description, task.due_at)
        result = TaskRepository(session).set_status_for_company(
            company.id,
            task.id,
            status,
        )
        assert result.changed is False
        assert (task.lead_id, task.title, task.description, task.due_at) == before
        task_id = task.id
        session.commit()
    assert task_status(task_id) == status.value


def test_todo_to_done_is_forbidden_and_persistence_is_unchanged() -> None:
    with seeded_task() as (session, _project, company, _contact, _lead, task):
        task_id = task.id
        with pytest.raises(
            TaskLifecycleRepositoryTransitionError,
            match=r"^Task status transition is not allowed\.$",
        ):
            TaskRepository(session).set_status_for_company(
                company.id,
                task.id,
                TaskLifecycleStatus.DONE,
            )
        session.rollback()
    assert task_status(task_id) == "TODO"


@pytest.mark.parametrize(
    "target",
    [
        TaskLifecycleStatus.TODO,
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.CANCELLED,
    ],
)
def test_done_is_terminal(target: TaskLifecycleStatus) -> None:
    with seeded_task(status="DONE") as (
        session,
        _project,
        company,
        _contact,
        _lead,
        task,
    ):
        with pytest.raises(TaskLifecycleRepositoryTransitionError) as exc_info:
            TaskRepository(session).set_status_for_company(
                company.id,
                task.id,
                target,
            )
        assert str(exc_info.value) == NOT_ALLOWED
        task_id = task.id
        session.rollback()
    assert task_status(task_id) == "DONE"


@pytest.mark.parametrize(
    "target",
    [
        TaskLifecycleStatus.TODO,
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.DONE,
    ],
)
def test_cancelled_is_terminal(target: TaskLifecycleStatus) -> None:
    with seeded_task(status="CANCELLED") as (
        session,
        _project,
        company,
        _contact,
        _lead,
        task,
    ):
        with pytest.raises(TaskLifecycleRepositoryTransitionError) as exc_info:
            TaskRepository(session).set_status_for_company(
                company.id,
                task.id,
                target,
            )
        assert str(exc_info.value) == NOT_ALLOWED
        task_id = task.id
        session.rollback()
    assert task_status(task_id) == "CANCELLED"


@pytest.mark.parametrize(
    ("company_id", "task_id"),
    [(2_147_483_647, 1), (1, 2_147_483_647)],
)
def test_missing_company_and_task_share_fixed_not_found(
    company_id: int,
    task_id: int,
) -> None:
    with seeded_task() as (session, _project, company, _contact, _lead, task):
        requested_company = company_id if company_id != 1 else company.id
        requested_task = task_id if task_id != 1 else task.id
        with pytest.raises(TaskLifecycleRepositoryNotFoundError) as exc_info:
            TaskRepository(session).set_status_for_company(
                requested_company,
                requested_task,
                TaskLifecycleStatus.IN_PROGRESS,
            )
        assert str(exc_info.value) == NOT_FOUND
        persisted_id = task.id
        session.rollback()
    assert task_status(persisted_id) == "TODO"


def test_cross_company_task_is_indistinguishable_from_not_found() -> None:
    with seeded_task() as (session, _project, _company, _contact, _lead, task):
        other_project = ProjectRepository(session).create("Other Project")
        other_company = CompanyRepository(session).create(
            project_id=other_project.id,
            name="Other Company",
        )
        with pytest.raises(TaskLifecycleRepositoryNotFoundError) as exc_info:
            TaskRepository(session).set_status_for_company(
                other_company.id,
                task.id,
                TaskLifecycleStatus.IN_PROGRESS,
            )
        assert str(exc_info.value) == NOT_FOUND
        task_id = task.id
        session.rollback()
    assert task_status(task_id) == "TODO"


def test_populate_existing_refreshes_stale_identity_map() -> None:
    with seeded_task() as (setup, _project, company, _contact, _lead, task):
        task_id = task.id
        company_id = company.id
    with SessionLocal() as session_a:
        stale = session_a.get(Task, task_id)
        assert stale is not None
        assert stale.status == "TODO"
        with SessionLocal() as session_b:
            TaskRepository(session_b).set_status_for_company(
                company_id,
                task_id,
                TaskLifecycleStatus.CANCELLED,
            )
            session_b.commit()
        assert stale.status == "TODO"
        with pytest.raises(TaskLifecycleRepositoryTransitionError):
            TaskRepository(session_a).set_status_for_company(
                company_id,
                task_id,
                TaskLifecycleStatus.IN_PROGRESS,
            )
        assert stale.status == "CANCELLED"
        session_a.rollback()
    assert task_status(task_id) == "CANCELLED"


@pytest.mark.parametrize("malformed", ["todo", " TODO", "", "WAITING_CUSTOMER"])
def test_malformed_persisted_status_is_not_exposed_or_repaired(
    malformed: str,
) -> None:
    with seeded_task() as (session, _project, company, _contact, _lead, task):
        snapshot = (task.lead_id, task.title, task.description, task.due_at)
        session.execute(
            text("UPDATE tasks SET status = :status WHERE id = :task_id"),
            {"status": malformed, "task_id": task.id},
        )
        session.commit()
        with pytest.raises(TaskLifecycleRepositoryTransitionError) as exc_info:
            TaskRepository(session).set_status_for_company(
                company.id,
                task.id,
                TaskLifecycleStatus.TODO,
            )
        assert str(exc_info.value) == NOT_ALLOWED
        if malformed:
            assert malformed not in str(exc_info.value)
        task_id = task.id
        session.rollback()
    with SessionLocal() as verification:
        stored = verification.get(Task, task_id)
        assert stored is not None
        assert stored.status == malformed
        assert (stored.lead_id, stored.title, stored.description, stored.due_at) == snapshot


def test_unknown_legacy_status_remains_readable_and_unchanged() -> None:
    with seeded_task(status="WAITING_CUSTOMER") as (
        session,
        _project,
        company,
        _contact,
        _lead,
        task,
    ):
        assert TaskRepository(session).get(task.id) is task
        with pytest.raises(TaskLifecycleRepositoryTransitionError):
            TaskRepository(session).set_status_for_company(
                company.id,
                task.id,
                TaskLifecycleStatus.TODO,
            )
        task_id = task.id
        session.rollback()
    assert task_status(task_id) == "WAITING_CUSTOMER"


def test_changed_transition_preserves_task_and_domain_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_network_guards(monkeypatch)
    due_at = datetime(2027, 2, 3, 4, 5)
    with seeded_task(due_at=due_at) as (
        session,
        project,
        company,
        contact,
        lead,
        task,
    ):
        counts = row_counts(session)
        domains = domain_snapshot(project, company, contact, lead)
        task_fields = (task.id, task.lead_id, task.title, task.description, task.due_at)
        result = TaskRepository(session).set_status_for_company(
            company.id,
            task.id,
            TaskLifecycleStatus.IN_PROGRESS,
        )
        assert result.task is task
        assert row_counts(session) == counts
        assert domain_snapshot(project, company, contact, lead) == domains
        assert (task.id, task.lead_id, task.title, task.description, task.due_at) == task_fields
        session.commit()


def test_creation_and_lead_creation_do_not_automatically_transition_tasks() -> None:
    with seeded_task() as (session, project, company, _contact, _lead, task):
        second_lead = LeadRepository(session).create(company_id=company.id)
        assert second_lead.id is not None
        created = TaskRepository(session).create_for_lead(
            lead_id=second_lead.id,
            title="New TODO task",
        )
        assert task.status == "TODO"
        assert created.status == "TODO"
        assert created.due_at is None
        assert project.id is not None
        session.rollback()


def test_legacy_generic_creation_keeps_custom_status_and_due_at() -> None:
    due_at = datetime(2028, 3, 4, 5, 6)
    with seeded_task() as (session, _project, _company, _contact, lead, _task):
        custom = TaskRepository(session).create(
            lead_id=lead.id,
            title="Legacy custom task",
            status="WAITING_CUSTOMER",
            due_at=due_at,
        )
        custom_id = custom.id
    with SessionLocal() as verification:
        stored = verification.get(Task, custom_id)
        assert stored is not None
        assert stored.status == "WAITING_CUSTOMER"
        assert stored.due_at == due_at

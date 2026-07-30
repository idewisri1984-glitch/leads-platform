import socket
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact.repository import ContactRepository
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository
from app.modules.task.lifecycle import (
    TaskLifecycleNotFoundError,
    TaskLifecycleService,
    TaskLifecycleTransitionError,
)
from app.modules.task.models import Task, TaskLifecycleStatus
from app.modules.task.repository import TaskRepository


@contextmanager
def seeded_task(
    status: str = "TODO",
    due_at: datetime | None = None,
) -> Iterator[tuple[Session, int, int, Task]]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Lifecycle Service Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Lifecycle Service Company",
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
            title="Preserved task",
            description="Preserved description",
            status=status,
            due_at=due_at,
        )
        yield session, company.id, lead.id, task


def service_for(session: Session) -> TaskLifecycleService:
    return TaskLifecycleService(TaskRepository(session))


def stored_status(task_id: int) -> str:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        return task.status


def test_caller_rollback_discards_flushed_transition() -> None:
    with seeded_task() as (session, company_id, _lead_id, task):
        result = service_for(session).transition(
            company_id,
            task.id,
            TaskLifecycleStatus.IN_PROGRESS,
        )
        task_id = task.id
        assert result.changed is True
        assert task.status == "IN_PROGRESS"
        session.rollback()
    assert stored_status(task_id) == "TODO"


def test_caller_commit_persists_transition() -> None:
    with seeded_task() as (session, company_id, _lead_id, task):
        result = service_for(session).transition(
            company_id,
            task.id,
            TaskLifecycleStatus.CANCELLED,
        )
        task_id = task.id
        session.commit()
    assert result.current_status is TaskLifecycleStatus.CANCELLED
    assert stored_status(task_id) == "CANCELLED"


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("TODO", TaskLifecycleStatus.IN_PROGRESS),
        ("TODO", TaskLifecycleStatus.CANCELLED),
        ("IN_PROGRESS", TaskLifecycleStatus.DONE),
        ("IN_PROGRESS", TaskLifecycleStatus.CANCELLED),
    ],
)
def test_complete_changed_matrix(
    previous: str,
    current: TaskLifecycleStatus,
) -> None:
    with seeded_task(previous) as (session, company_id, _lead_id, task):
        task_id = task.id
        result = service_for(session).transition(company_id, task.id, current)
        assert result.changed is True
        session.commit()
    assert stored_status(task_id) == current.value


@pytest.mark.parametrize("status", list(TaskLifecycleStatus))
def test_complete_idempotent_matrix(status: TaskLifecycleStatus) -> None:
    with seeded_task(status.value) as (session, company_id, lead_id, task):
        before = (task.title, task.description, task.due_at, task.lead_id)
        result = service_for(session).transition(company_id, task.id, status)
        assert result.changed is False
        assert (task.title, task.description, task.due_at, task.lead_id) == before
        assert task.lead_id == lead_id


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("TODO", TaskLifecycleStatus.DONE),
        ("IN_PROGRESS", TaskLifecycleStatus.TODO),
        ("DONE", TaskLifecycleStatus.TODO),
        ("DONE", TaskLifecycleStatus.IN_PROGRESS),
        ("DONE", TaskLifecycleStatus.CANCELLED),
        ("CANCELLED", TaskLifecycleStatus.TODO),
        ("CANCELLED", TaskLifecycleStatus.IN_PROGRESS),
        ("CANCELLED", TaskLifecycleStatus.DONE),
    ],
)
def test_complete_forbidden_matrix(
    previous: str,
    current: TaskLifecycleStatus,
) -> None:
    with seeded_task(previous) as (session, company_id, _lead_id, task):
        task_id = task.id
        with pytest.raises(
            TaskLifecycleTransitionError,
            match=r"^Task status transition is not allowed\.$",
        ):
            service_for(session).transition(company_id, task.id, current)
        session.rollback()
    assert stored_status(task_id) == previous


@pytest.mark.parametrize("missing", ["company", "task"])
def test_missing_scope_has_fixed_not_found(missing: str) -> None:
    with seeded_task() as (session, company_id, _lead_id, task):
        requested_company = 2_147_483_647 if missing == "company" else company_id
        requested_task = 2_147_483_647 if missing == "task" else task.id
        with pytest.raises(
            TaskLifecycleNotFoundError,
            match=r"^Task was not found\.$",
        ):
            service_for(session).transition(
                requested_company,
                requested_task,
                TaskLifecycleStatus.IN_PROGRESS,
            )


def test_cross_company_task_is_hidden() -> None:
    with seeded_task() as (session, _company_id, _lead_id, task):
        project = ProjectRepository(session).create("Other Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name="Other Company",
        )
        with pytest.raises(TaskLifecycleNotFoundError):
            service_for(session).transition(
                company.id,
                task.id,
                TaskLifecycleStatus.IN_PROGRESS,
            )


def test_stale_identity_map_is_refreshed_through_service() -> None:
    with seeded_task() as (_session, company_id, _lead_id, task):
        task_id = task.id
    with SessionLocal() as session_a:
        stale = session_a.get(Task, task_id)
        assert stale is not None and stale.status == "TODO"
        with SessionLocal() as session_b:
            service_for(session_b).transition(
                company_id,
                task_id,
                TaskLifecycleStatus.CANCELLED,
            )
            session_b.commit()
        assert stale.status == "TODO"
        with pytest.raises(TaskLifecycleTransitionError):
            service_for(session_a).transition(
                company_id,
                task_id,
                TaskLifecycleStatus.IN_PROGRESS,
            )
        assert stale.status == "CANCELLED"
        session_a.rollback()
    assert stored_status(task_id) == "CANCELLED"


@pytest.mark.parametrize("status", ["todo", " TODO", "", "WAITING_CUSTOMER"])
def test_malformed_and_legacy_statuses_are_not_repaired(status: str) -> None:
    with seeded_task() as (session, company_id, _lead_id, task):
        session.execute(
            text("UPDATE tasks SET status=:status WHERE id=:task_id"),
            {"status": status, "task_id": task.id},
        )
        session.commit()
        task_id = task.id
        with pytest.raises(TaskLifecycleTransitionError) as exc_info:
            service_for(session).transition(
                company_id,
                task.id,
                TaskLifecycleStatus.TODO,
            )
        assert str(exc_info.value) == "Task status transition is not allowed."
        if status:
            assert status not in str(exc_info.value)
        session.rollback()
    assert stored_status(task_id) == status


def test_domain_fields_network_and_legacy_creation_remain_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network operation attempted.")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    due_at = datetime(2028, 1, 2, 3, 4)
    with seeded_task(due_at=due_at) as (session, company_id, lead_id, task):
        before = (task.title, task.description, task.due_at, task.lead_id)
        service_for(session).transition(
            company_id,
            task.id,
            TaskLifecycleStatus.IN_PROGRESS,
        )
        assert (task.title, task.description, task.due_at, task.lead_id) == before
        custom = TaskRepository(session).create(
            lead_id=lead_id,
            title="Legacy custom",
            status="WAITING_CUSTOMER",
            due_at=due_at,
        )
        assert custom.status == "WAITING_CUSTOMER"
        confirmed = TaskRepository(session).create_for_lead(
            lead_id=lead_id,
            title="Confirmed",
        )
        assert confirmed.status == "TODO"
        assert confirmed.due_at is None
        session.rollback()

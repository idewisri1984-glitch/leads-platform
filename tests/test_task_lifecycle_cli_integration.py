import socket
import urllib.request
from collections.abc import Callable
from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.main import app as root_app
from app.cli.task import (
    TaskLifecycleCommandOutcome,
    execute_task_lifecycle_transition,
)
from app.core.database.engine import engine
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.models import Project
from app.modules.project.repository import ProjectRepository
from app.modules.task import TaskLifecycleStatus
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository

runner = CliRunner()


class TrackingSession(Session):
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        fail_close: bool = False,
    ) -> None:
        super().__init__(bind=engine, expire_on_commit=False)
        self.fail_commit = fail_commit
        self.fail_close = fail_close
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
        if self.fail_close:
            raise RuntimeError("private close failure")


def tracking_factory(
    *,
    fail_commit: bool = False,
    fail_close: bool = False,
) -> tuple[Callable[[], Session], list[TrackingSession]]:
    sessions: list[TrackingSession] = []

    def factory() -> Session:
        session = TrackingSession(
            fail_commit=fail_commit,
            fail_close=fail_close,
        )
        sessions.append(session)
        return session

    return factory, sessions


def seed_task(
    *,
    status: str = "TODO",
    company_name: str = "Lifecycle Company",
) -> tuple[int, int, int, int, int]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Lifecycle Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name=company_name,
        )
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
            email="ada@example.com",
        )
        lead = LeadRepository(session).create(
            company_id=company.id,
            contact_id=contact.id,
        )
        task = TaskRepository(session).create(
            lead_id=lead.id,
            title="Private lifecycle title",
            description="Private lifecycle description",
            status=status,
            due_at=datetime(2026, 8, 1, 10, 30),
        )
        session.commit()
        return project.id, company.id, contact.id, lead.id, task.id


def invoke(command: str, company_id: int, task_id: int, *, yes: bool = True) -> object:
    arguments = [
        "task",
        command,
        "--company-id",
        str(company_id),
        "--task-id",
        str(task_id),
    ]
    if yes:
        arguments.append("--yes")
    return runner.invoke(root_app, arguments)


def persisted_task(task_id: int) -> Task:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task


@pytest.mark.parametrize(
    ("initial", "command", "expected", "changed"),
    [
        ("TODO", "start", "IN_PROGRESS", True),
        ("IN_PROGRESS", "start", "IN_PROGRESS", False),
        ("IN_PROGRESS", "complete", "DONE", True),
        ("DONE", "complete", "DONE", False),
        ("TODO", "cancel", "CANCELLED", True),
        ("IN_PROGRESS", "cancel", "CANCELLED", True),
        ("CANCELLED", "cancel", "CANCELLED", False),
    ],
)
def test_successful_commands_persist_exact_transition_and_safe_output(
    initial: str,
    command: str,
    expected: str,
    changed: bool,
) -> None:
    _, company_id, _, lead_id, task_id = seed_task(status=initial)
    before = persisted_task(task_id)
    result = invoke(command, company_id, task_id)
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        f"Task ID: {task_id}",
        f"Company ID: {company_id}",
        f"Previous Status: {initial}",
        f"Current Status: {expected}",
        f"Changed: {'true' if changed else 'false'}",
    ]
    after = persisted_task(task_id)
    assert after.status == expected
    assert (
        after.id,
        after.lead_id,
        after.title,
        after.description,
        after.due_at,
    ) == (
        before.id,
        lead_id,
        before.title,
        before.description,
        before.due_at,
    )


@pytest.mark.parametrize(
    ("initial", "command"),
    [
        ("TODO", "complete"),
        ("DONE", "start"),
        ("DONE", "cancel"),
        ("CANCELLED", "start"),
        ("CANCELLED", "complete"),
    ],
)
def test_forbidden_and_terminal_transitions_roll_back(
    initial: str,
    command: str,
) -> None:
    _, company_id, _, _, task_id = seed_task(status=initial)
    result = invoke(command, company_id, task_id)
    assert result.exit_code == 1
    assert result.output.strip() == "Task status transition is not allowed."
    assert persisted_task(task_id).status == initial
    assert "Private" not in result.output


@pytest.mark.parametrize("command", ["start", "complete", "cancel"])
def test_missing_confirmation_never_mutates(command: str) -> None:
    _, company_id, _, _, task_id = seed_task()
    result = invoke(command, company_id, task_id, yes=False)
    assert result.exit_code == 1
    assert result.output.strip() == "Task lifecycle transition requires --yes."
    assert persisted_task(task_id).status == "TODO"


def test_invalid_identifiers_are_rejected_before_session_creation() -> None:
    calls: list[str] = []
    for arguments in (
        {"company_id": 0, "task_id": 1},
        {"company_id": 1, "task_id": 0},
    ):
        outcome = execute_task_lifecycle_transition(
            **arguments,
            target_status=TaskLifecycleStatus.IN_PROGRESS,
            yes=True,
            session_factory=lambda: calls.append("session"),  # type: ignore[arg-type]
        )
        assert outcome.error_message == "Task lifecycle data is invalid."
    assert calls == []


@pytest.mark.parametrize("missing", ["company", "task"])
def test_missing_scope_returns_fixed_not_found(missing: str) -> None:
    _, company_id, _, _, task_id = seed_task()
    result = invoke(
        "start",
        2_147_483_647 if missing == "company" else company_id,
        2_147_483_647 if missing == "task" else task_id,
    )
    assert result.exit_code == 1
    assert result.output.strip() == "Task was not found."
    assert persisted_task(task_id).status == "TODO"


def test_cross_company_task_is_hidden_without_mutation() -> None:
    _, first_company, _, _, task_id = seed_task(company_name="First")
    _, second_company, _, _, _ = seed_task(company_name="Second")
    result = invoke("start", second_company, task_id)
    assert first_company != second_company
    assert result.exit_code == 1
    assert result.output.strip() == "Task was not found."
    assert persisted_task(task_id).status == "TODO"


@pytest.mark.parametrize("status", ["", "WAITING_CUSTOMER"])
def test_malformed_and_unknown_legacy_status_are_not_repaired(status: str) -> None:
    _, company_id, _, _, task_id = seed_task(status=status)
    shown = runner.invoke(root_app, ["task", "show", str(task_id)])
    assert shown.exit_code == 0
    assert f"Status:      {status}" in shown.output
    result = invoke("start", company_id, task_id)
    assert result.exit_code == 1
    assert result.output.strip() == "Task status transition is not allowed."
    assert persisted_task(task_id).status == status


def test_sequential_start_and_complete_reaches_done() -> None:
    _, company_id, _, _, task_id = seed_task()
    started = invoke("start", company_id, task_id)
    completed = invoke("complete", company_id, task_id)
    assert started.exit_code == completed.exit_code == 0
    assert "Previous Status: TODO" in started.output
    assert "Current Status: IN_PROGRESS" in started.output
    assert "Previous Status: IN_PROGRESS" in completed.output
    assert "Current Status: DONE" in completed.output
    assert persisted_task(task_id).status == "DONE"


def test_commit_failure_rolls_back_real_flushed_transition() -> None:
    _, company_id, _, _, task_id = seed_task()
    factory, sessions = tracking_factory(fail_commit=True)
    outcome = execute_task_lifecycle_transition(
        company_id=company_id,
        task_id=task_id,
        target_status=TaskLifecycleStatus.IN_PROGRESS,
        yes=True,
        session_factory=factory,
    )
    assert outcome == TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        1,
        1,
        1,
    )
    assert persisted_task(task_id).status == "TODO"


def test_close_failure_after_commit_preserves_change_but_exposes_no_result() -> None:
    _, company_id, _, _, task_id = seed_task()
    factory, sessions = tracking_factory(fail_close=True)
    outcome = execute_task_lifecycle_transition(
        company_id=company_id,
        task_id=task_id,
        target_status=TaskLifecycleStatus.IN_PROGRESS,
        yes=True,
        session_factory=factory,
    )
    assert outcome == TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        1,
        0,
        1,
    )
    assert persisted_task(task_id).status == "IN_PROGRESS"


def test_domain_rows_and_task_fields_remain_immutable() -> None:
    project_id, company_id, contact_id, lead_id, task_id = seed_task()
    with SessionLocal() as session:
        before_counts = (
            session.scalar(select(func.count()).select_from(Project)),
            session.scalar(select(func.count()).select_from(Company)),
            session.scalar(select(func.count()).select_from(Contact)),
            session.scalar(select(func.count()).select_from(Lead)),
            session.scalar(select(func.count()).select_from(Task)),
        )
        project_name = session.get(Project, project_id).name  # type: ignore[union-attr]
        company_name = session.get(Company, company_id).name  # type: ignore[union-attr]
        contact_email = session.get(Contact, contact_id).email  # type: ignore[union-attr]
        lead_contact_id = session.get(Lead, lead_id).contact_id  # type: ignore[union-attr]
    result = invoke("cancel", company_id, task_id)
    assert result.exit_code == 0
    with SessionLocal() as session:
        after_counts = (
            session.scalar(select(func.count()).select_from(Project)),
            session.scalar(select(func.count()).select_from(Company)),
            session.scalar(select(func.count()).select_from(Contact)),
            session.scalar(select(func.count()).select_from(Lead)),
            session.scalar(select(func.count()).select_from(Task)),
        )
        assert after_counts == before_counts
        assert session.get(Project, project_id).name == project_name  # type: ignore[union-attr]
        assert session.get(Company, company_id).name == company_name  # type: ignore[union-attr]
        assert session.get(Contact, contact_id).email == contact_email  # type: ignore[union-attr]
        assert session.get(Lead, lead_id).contact_id == lead_contact_id  # type: ignore[union-attr]


def test_success_output_excludes_private_domain_data() -> None:
    _, company_id, contact_id, lead_id, task_id = seed_task(company_name="Private Company Name")
    result = invoke("start", company_id, task_id)
    assert result.exit_code == 0
    for forbidden in (
        "Private lifecycle title",
        "Private lifecycle description",
        f"Lead ID: {lead_id}",
        str(datetime(2026, 8, 1, 10, 30)),
        "ada@example.com",
        "Private Company Name",
        f"Contact ID: {contact_id}",
        "SELECT ",
        "sqlite",
        "\\",
    ):
        assert forbidden not in result.output


def test_lifecycle_command_does_not_use_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id, _, _, task_id = seed_task()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network operation attempted")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    result = invoke("start", company_id, task_id)
    assert result.exit_code == 0


def test_legacy_task_commands_and_confirmed_creation_remain_operational() -> None:
    _, company_id, _, lead_id, lifecycle_task_id = seed_task()
    created = runner.invoke(
        root_app,
        [
            "task",
            "create",
            str(lead_id),
            "Legacy custom task",
            "--status",
            "WAITING_CUSTOMER",
            "--due-at",
            "2026-08-02T11:00:00",
        ],
    )
    assert created.exit_code == 0
    with SessionLocal() as session:
        custom = session.scalar(select(Task).where(Task.title == "Legacy custom task"))
        assert custom is not None
        custom_id = custom.id
        assert (custom.status, custom.due_at) == (
            "WAITING_CUSTOMER",
            datetime(2026, 8, 2, 11, 0),
        )
    listed = runner.invoke(root_app, ["task", "list"])
    shown = runner.invoke(root_app, ["task", "show", str(custom_id)])
    deleted = runner.invoke(root_app, ["task", "delete", str(custom_id)])
    confirmed = runner.invoke(
        root_app,
        [
            "task",
            "create-for-lead",
            "--company-id",
            str(company_id),
            "--lead-id",
            str(lead_id),
            "--title",
            "Confirmed creation",
            "--yes",
        ],
    )
    assert listed.exit_code == shown.exit_code == deleted.exit_code == 0
    assert confirmed.exit_code == 0
    with SessionLocal() as session:
        lifecycle_task = session.get(Task, lifecycle_task_id)
        confirmed_task = session.scalar(select(Task).where(Task.title == "Confirmed creation"))
        assert lifecycle_task is not None and lifecycle_task.status == "TODO"
        assert confirmed_task is not None
        assert (confirmed_task.status, confirmed_task.due_at) == ("TODO", None)

import socket
import urllib.request
from collections.abc import Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.lead import execute_create_lead_from_contact
from app.cli.main import app as root_app
from app.cli.task import LeadTaskCreationCommandOutcome, execute_create_task_for_lead
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
from app.modules.task.models import Task

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


def seed_company_lead(
    *,
    company_name: str = "Task CLI Company",
) -> tuple[int, int, int, int]:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Task CLI Project")
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
        session.commit()
        return project.id, company.id, contact.id, lead.id


def table_counts(session: Session) -> tuple[int, int, int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(Project)) or 0,
        session.scalar(select(func.count()).select_from(Company)) or 0,
        session.scalar(select(func.count()).select_from(Contact)) or 0,
        session.scalar(select(func.count()).select_from(Lead)) or 0,
        session.scalar(select(func.count()).select_from(Task)) or 0,
    )


def invoke_confirmed(
    company_id: int,
    lead_id: int,
    *,
    title: str = "Follow up",
    description: str | None = "Call tomorrow",
) -> object:
    arguments = [
        "task",
        "create-for-lead",
        "--company-id",
        str(company_id),
        "--lead-id",
        str(lead_id),
        "--title",
        title,
    ]
    if description is not None:
        arguments.extend(["--description", description])
    arguments.append("--yes")
    return runner.invoke(root_app, arguments)


def test_confirmed_command_commits_exact_task_and_safe_output() -> None:
    _, company_id, _, lead_id = seed_company_lead()
    result = invoke_confirmed(company_id, lead_id)
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[1:] == [
        f"Company ID: {company_id}",
        f"Lead ID: {lead_id}",
        "Status: TODO",
    ]
    assert lines[0].startswith("Task ID: ")
    with SessionLocal() as verification:
        tasks = verification.scalars(select(Task)).all()
        assert len(tasks) == 1
        task = tasks[0]
        assert lines[0] == f"Task ID: {task.id}"
        assert (task.lead_id, task.title, task.description, task.status, task.due_at) == (
            lead_id,
            "Follow up",
            "Call tomorrow",
            "TODO",
            None,
        )


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Omitted description", None),
        ("Empty description", ""),
        ("  Padded title  ", "  Padded description  "),
    ],
)
def test_description_and_text_are_persisted_exactly(
    title: str,
    description: str | None,
) -> None:
    _, company_id, _, lead_id = seed_company_lead()
    result = invoke_confirmed(
        company_id,
        lead_id,
        title=title,
        description=description,
    )
    assert result.exit_code == 0
    assert title not in result.output
    if description:
        assert description not in result.output
    with SessionLocal() as verification:
        task = verification.scalar(select(Task))
        assert task is not None
        assert task.title == title
        assert task.description == description


def test_missing_confirmation_and_invalid_input_create_no_session_or_task() -> None:
    _, company_id, _, lead_id = seed_company_lead()
    calls: list[str] = []
    unconfirmed = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=False,
        session_factory=lambda: calls.append("session"),  # type: ignore[arg-type]
    )
    invalid = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title=" ",
        yes=True,
        session_factory=lambda: calls.append("session"),  # type: ignore[arg-type]
    )
    assert unconfirmed.error_message == "Task creation requires --yes."
    assert invalid.error_message == "Task creation data is invalid."
    assert calls == []
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("missing", ["company", "lead"])
def test_missing_scope_has_fixed_not_found_and_no_task(missing: str) -> None:
    _, company_id, _, lead_id = seed_company_lead()
    outcome = execute_create_task_for_lead(
        company_id=2_147_483_647 if missing == "company" else company_id,
        lead_id=2_147_483_647 if missing == "lead" else lead_id,
        title="Follow up",
        yes=True,
    )
    assert outcome == LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message="Lead was not found.",
    )
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_cross_company_lead_is_hidden_without_mutation() -> None:
    _, first_company, _, lead_id = seed_company_lead(company_name="First")
    seed_company_lead(company_name="Second")
    with SessionLocal() as session:
        companies = session.scalars(select(Company).order_by(Company.id)).all()
        second_company = companies[-1]
        before = table_counts(session)
    outcome = execute_create_task_for_lead(
        company_id=second_company.id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
    )
    assert first_company != second_company.id
    assert outcome.error_message == "Lead was not found."
    with SessionLocal() as verification:
        assert table_counts(verification) == before


def test_repeated_confirmed_execution_commits_two_distinct_tasks() -> None:
    _, company_id, _, lead_id = seed_company_lead()
    first = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
    )
    second = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
    )
    assert first.result is not None and second.result is not None
    assert first.result.task_id != second.result.task_id
    with SessionLocal() as verification:
        tasks = verification.scalars(select(Task).order_by(Task.id)).all()
        assert [task.id for task in tasks] == [
            first.result.task_id,
            second.result.task_id,
        ]


def test_commit_failure_rolls_back_real_flushed_task() -> None:
    _, company_id, _, lead_id = seed_company_lead()
    factory, sessions = tracking_factory(fail_commit=True)
    outcome = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
        session_factory=factory,
    )
    assert outcome.error_message == "Task creation failed."
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        1,
        1,
        1,
    )
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Task)) == 0
        assert verification.get(Lead, lead_id) is not None


def test_close_failure_after_commit_reports_failure_but_preserves_task() -> None:
    _, company_id, _, lead_id = seed_company_lead()
    factory, sessions = tracking_factory(fail_close=True)
    outcome = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
        session_factory=factory,
    )
    assert outcome == LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message="Task creation failed.",
    )
    assert (sessions[0].commit_calls, sessions[0].rollback_calls, sessions[0].close_calls) == (
        1,
        0,
        1,
    )
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Task)) == 1


def test_success_and_failure_preserve_other_domain_rows() -> None:
    _, company_id, _, lead_id = seed_company_lead()
    with SessionLocal() as before:
        counts = table_counts(before)
    success = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
    )
    failure = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=2_147_483_647,
        title="Follow up",
        yes=True,
    )
    assert success.exit_code == 0 and failure.exit_code == 1
    with SessionLocal() as verification:
        after = table_counts(verification)
        assert after[:4] == counts[:4]
        assert after[4] == 1


def test_confirmed_execution_does_not_use_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id, _, lead_id = seed_company_lead()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network operation attempted")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    outcome = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title="Follow up",
        yes=True,
    )
    assert outcome.exit_code == 0


def test_contact_scoped_lead_creation_still_creates_no_task() -> None:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Automatic Task Guard")
        company = CompanyRepository(session).create(project_id=project.id, name="Guard")
        contact = ContactRepository(session).create(
            company_id=company.id,
            first_name="Ada",
            email="guard@example.com",
        )
        session.commit()
        company_id, contact_id = company.id, contact.id
    lead_outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=True,
    )
    assert lead_outcome.exit_code == 0
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_legacy_task_commands_remain_operational() -> None:
    _, _, _, lead_id = seed_company_lead()
    created = runner.invoke(
        root_app,
        [
            "task",
            "create",
            str(lead_id),
            "Legacy task",
            "--description",
            "Legacy description",
            "--status",
            "DONE",
            "--due-at",
            "2026-07-30T10:00:00",
        ],
    )
    assert created.exit_code == 0
    assert "Task created" in created.output
    assert "Title: Legacy task" in created.output
    assert "Status: DONE" in created.output
    assert "Due at: 2026-07-30 10:00:00" in created.output
    with SessionLocal() as verification:
        task = verification.scalar(select(Task))
        assert task is not None
        task_id = task.id
    listed = runner.invoke(root_app, ["task", "list"])
    shown = runner.invoke(root_app, ["task", "show", str(task_id)])
    deleted = runner.invoke(root_app, ["task", "delete", str(task_id)])
    assert listed.exit_code == shown.exit_code == deleted.exit_code == 0
    assert "Legacy task" in listed.output
    assert "Legacy task" in shown.output
    assert "Task deleted" in deleted.output

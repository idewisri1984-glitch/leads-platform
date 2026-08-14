from __future__ import annotations

import json
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database.engine import engine
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.outreach_completion import (
    CompanyScopedOutreachCompletionCompanyNotFoundError,
    CompanyScopedOutreachCompletionComponents,
    CompanyScopedOutreachCompletionConflictError,
    CompanyScopedOutreachCompletionInput,
    CompanyScopedOutreachCompletionInvalidDataError,
    CompanyScopedOutreachCompletionPersistenceError,
    CompanyScopedOutreachCompletionProjectNotFoundError,
    CompanyScopedOutreachCompletionService,
)
from app.modules.company.repository import CompanyRepository
from app.modules.company_enrichment.models import CompanyEnrichment, EnrichmentStatus
from app.modules.company_enrichment.repository import CompanyEnrichmentRepository
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.models import Project
from app.modules.project.repository import ProjectRepository
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository

_EMAIL = "hello@studio.example"
_TITLE = "Prepare personalized company outreach email"
_DESCRIPTION = "Prepare a personalized manual outreach email for the company recipient."


class TrackingSession(Session):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_count += 1
        super().rollback()

    def close(self) -> None:
        self.close_count += 1
        super().close()


class TrackingFactory:
    def __init__(self) -> None:
        self._factory = sessionmaker(bind=engine, class_=TrackingSession)
        self.sessions: list[TrackingSession] = []

    def __call__(self) -> TrackingSession:
        session = self._factory()
        self.sessions.append(session)
        return session


def seed_company(*, email: str = _EMAIL) -> tuple[int, int]:
    with SessionLocal() as session:
        project = Project(name="Project")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Studio")
        session.add(company)
        session.flush()
        session.add(
            CompanyEnrichment(
                company_id=company.id,
                enrichment_status=EnrichmentStatus.SUCCEEDED,
                email=email,
            )
        )
        session.commit()
        return project.id, company.id


def complete(
    factory: TrackingFactory,
    project_id: int,
    company_id: int,
    *,
    components: CompanyScopedOutreachCompletionComponents | None = None,
) -> object:
    return CompanyScopedOutreachCompletionService(
        session_factory=factory,
        components=components,
    ).complete(
        CompanyScopedOutreachCompletionInput(
            project_id=project_id,
            company_id=company_id,
            trusted_recipient_email=f"  {_EMAIL.upper()}  ",
        )
    )


def counts() -> tuple[int, int, int]:
    with SessionLocal() as session:
        return (
            session.scalar(select(func.count()).select_from(Lead)) or 0,
            session.scalar(select(func.count()).select_from(Task)) or 0,
            session.scalar(select(func.count()).select_from(Contact)) or 0,
        )


def seed_duplicate_leads(
    company_id: int,
    statuses: tuple[str | None, str | None],
) -> tuple[tuple[int, int], tuple[int | None, int | None]]:
    with SessionLocal() as session:
        leads = [
            Lead(company_id=company_id, contact_id=None, status="NEW"),
            Lead(company_id=company_id, contact_id=None, status="NEW"),
        ]
        session.add_all(leads)
        session.flush()
        task_ids: list[int | None] = []
        for lead, status in zip(leads, statuses, strict=True):
            if status is None:
                task_ids.append(None)
                continue
            task = Task(
                lead_id=lead.id,
                title=_TITLE,
                description=_DESCRIPTION,
                status=status,
            )
            session.add(task)
            session.flush()
            task_ids.append(task.id)
        session.commit()
        return (leads[0].id, leads[1].id), (task_ids[0], task_ids[1])


def test_first_completion_creates_company_lead_and_outreach_task_atomically() -> None:
    project_id, company_id = seed_company()
    factory = TrackingFactory()

    result = complete(factory, project_id, company_id)

    assert result.project_id == project_id
    assert result.company_id == company_id
    assert result.contact_id is None
    assert result.lead_created is True
    assert result.lead_reused is False
    assert result.task_created is True
    assert result.task_reused is False
    assert counts() == (1, 1, 0)
    assert (
        factory.sessions[0].commit_count,
        factory.sessions[0].rollback_count,
        factory.sessions[0].close_count,
    ) == (1, 0, 1)
    with SessionLocal() as session:
        lead = session.get(Lead, result.lead_id)
        task = session.get(Task, result.task_id)
        assert lead is not None and lead.contact_id is None
        assert lead.company_id == company_id
        assert task is not None and task.lead_id == lead.id
        assert (task.title, task.description, task.status, task.due_at) == (
            _TITLE,
            _DESCRIPTION,
            "TODO",
            None,
        )


def test_exact_rerun_reuses_same_lead_and_task_without_duplicate_rows() -> None:
    project_id, company_id = seed_company()
    factory = TrackingFactory()

    first = complete(factory, project_id, company_id)
    second = complete(factory, project_id, company_id)

    assert second.lead_id == first.lead_id
    assert second.task_id == first.task_id
    assert second.lead_created is False
    assert second.lead_reused is True
    assert second.task_created is False
    assert second.task_reused is True
    assert second.contact_id is None
    assert counts() == (1, 1, 0)
    transaction_counts = [
        (item.commit_count, item.rollback_count, item.close_count) for item in factory.sessions
    ]
    assert transaction_counts == [
        (1, 0, 1),
        (1, 0, 1),
    ]


def test_existing_company_lead_is_reused_when_task_is_missing() -> None:
    project_id, company_id = seed_company()
    with SessionLocal() as session:
        lead = Lead(company_id=company_id, contact_id=None, status="NEW")
        session.add(lead)
        session.commit()
        lead_id = lead.id

    result = complete(TrackingFactory(), project_id, company_id)

    assert result.lead_id == lead_id
    assert result.lead_reused is True
    assert result.task_created is True
    assert counts() == (1, 1, 0)


def test_existing_active_task_is_reused_with_company_lead() -> None:
    project_id, company_id = seed_company()
    with SessionLocal() as session:
        lead = Lead(company_id=company_id, contact_id=None, status="NEW")
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id,
            title=_TITLE,
            description=_DESCRIPTION,
            status="IN_PROGRESS",
        )
        session.add(task)
        session.commit()
        lead_id, task_id = lead.id, task.id

    result = complete(TrackingFactory(), project_id, company_id)

    assert (result.lead_id, result.task_id) == (lead_id, task_id)
    assert result.lead_reused is result.task_reused is True
    assert result.lead_created is result.task_created is False
    assert counts() == (1, 1, 0)


@pytest.mark.parametrize("status", ["TODO", "IN_PROGRESS"])
def test_duplicate_leads_reuse_active_task_on_higher_id_lead(status: str) -> None:
    project_id, company_id = seed_company()
    lead_ids, task_ids = seed_duplicate_leads(company_id, (None, status))

    result = complete(TrackingFactory(), project_id, company_id)

    assert result.lead_id == lead_ids[1]
    assert result.task_id == task_ids[1]
    assert result.lead_created is False
    assert result.lead_reused is True
    assert result.task_created is False
    assert result.task_reused is True
    assert result.lead_id != lead_ids[0]
    assert counts() == (2, 1, 0)


def test_duplicate_leads_without_active_task_use_minimum_id_then_reuse() -> None:
    project_id, company_id = seed_company()
    lead_ids, _ = seed_duplicate_leads(company_id, (None, None))
    factory = TrackingFactory()

    first = complete(factory, project_id, company_id)
    second = complete(factory, project_id, company_id)

    assert first.lead_id == min(lead_ids)
    assert first.task_created is True
    assert second.lead_id == first.lead_id
    assert second.task_id == first.task_id
    assert second.task_reused is True
    assert counts() == (2, 1, 0)


def test_multiple_active_tasks_across_duplicate_leads_are_conflict_without_mutation() -> None:
    project_id, company_id = seed_company()
    seed_duplicate_leads(company_id, ("TODO", "IN_PROGRESS"))
    factory = TrackingFactory()

    with pytest.raises(CompanyScopedOutreachCompletionConflictError) as captured:
        complete(factory, project_id, company_id)

    assert str(captured.value) == "Company-scoped outreach completion found conflicting state."
    assert captured.value.__cause__ is captured.value.__context__ is None
    assert counts() == (2, 2, 0)
    assert (
        factory.sessions[0].commit_count,
        factory.sessions[0].rollback_count,
        factory.sessions[0].close_count,
    ) == (0, 1, 1)


@pytest.mark.parametrize("terminal_status", ["DONE", "CANCELLED"])
def test_terminal_task_creates_one_todo_then_rerun_reuses_it(terminal_status: str) -> None:
    project_id, company_id = seed_company()
    with SessionLocal() as session:
        lead = Lead(company_id=company_id, contact_id=None, status="NEW")
        session.add(lead)
        session.flush()
        terminal = Task(
            lead_id=lead.id,
            title=_TITLE,
            description=_DESCRIPTION,
            status=terminal_status,
        )
        session.add(terminal)
        session.commit()
        lead_id, terminal_id = lead.id, terminal.id
    factory = TrackingFactory()

    first = complete(factory, project_id, company_id)
    second = complete(factory, project_id, company_id)

    assert first.lead_id == lead_id
    assert first.task_id != terminal_id
    assert first.task_created is True
    assert second.lead_id == first.lead_id
    assert second.task_id == first.task_id
    assert second.task_reused is True
    with SessionLocal() as session:
        tasks = list(session.scalars(select(Task).where(Task.lead_id == lead_id).order_by(Task.id)))
        assert [(task.id, task.status) for task in tasks] == [
            (terminal_id, terminal_status),
            (first.task_id, "TODO"),
        ]


def test_late_task_failure_rolls_back_new_lead_and_closes_session() -> None:
    project_id, company_id = seed_company()
    factory = TrackingFactory()

    class FailingTaskRepository(TaskRepository):
        def create_for_lead(self, **kwargs: object) -> Task:
            raise RuntimeError("sqlite:///private.db secret@example.test")

    components = replace(
        CompanyScopedOutreachCompletionComponents(),
        task_repository=FailingTaskRepository,
    )

    with pytest.raises(CompanyScopedOutreachCompletionPersistenceError) as captured:
        complete(factory, project_id, company_id, components=components)

    assert str(captured.value) == "Company-scoped outreach completion could not be persisted."
    assert captured.value.__cause__ is captured.value.__context__ is None
    rendered = "".join(traceback.format_exception(captured.value))
    assert "private.db" not in rendered
    assert "secret@example.test" not in rendered
    assert counts() == (0, 0, 0)
    assert (
        factory.sessions[0].commit_count,
        factory.sessions[0].rollback_count,
        factory.sessions[0].close_count,
    ) == (0, 1, 1)


def test_missing_project_is_typed_and_writes_nothing() -> None:
    factory = TrackingFactory()

    with pytest.raises(
        CompanyScopedOutreachCompletionProjectNotFoundError,
        match=r"^Project was not found\.$",
    ):
        complete(factory, 999, 999)

    assert counts() == (0, 0, 0)
    assert (factory.sessions[0].commit_count, factory.sessions[0].rollback_count) == (0, 1)
    assert factory.sessions[0].close_count == 1


def test_company_must_exist_in_requested_project() -> None:
    project_id, company_id = seed_company()
    with SessionLocal() as session:
        other = Project(name="Other")
        session.add(other)
        session.commit()
        other_project_id = other.id
    factory = TrackingFactory()

    with pytest.raises(
        CompanyScopedOutreachCompletionCompanyNotFoundError,
        match=r"^Company was not found\.$",
    ):
        complete(factory, other_project_id, company_id)

    assert counts() == (0, 0, 0)
    assert (factory.sessions[0].commit_count, factory.sessions[0].rollback_count) == (0, 1)


def test_trusted_email_must_match_persisted_company_email() -> None:
    project_id, company_id = seed_company(email="different@studio.example")

    with pytest.raises(
        CompanyScopedOutreachCompletionConflictError,
        match=r"^Trusted company email does not match persisted company data\.$",
    ):
        complete(TrackingFactory(), project_id, company_id)

    assert counts() == (0, 0, 0)


def test_invalid_trusted_email_has_no_internal_exception_context() -> None:
    project_id, company_id = seed_company()
    service = CompanyScopedOutreachCompletionService(session_factory=TrackingFactory())

    with pytest.raises(CompanyScopedOutreachCompletionInvalidDataError) as captured:
        service.complete(
            CompanyScopedOutreachCompletionInput(
                project_id=project_id,
                company_id=company_id,
                trusted_recipient_email="invalid",
            )
        )

    assert str(captured.value) == "Company-scoped outreach completion data is invalid."
    assert captured.value.__cause__ is captured.value.__context__ is None
    assert counts() == (0, 0, 0)


def test_lead_repository_create_still_commits_and_refreshes() -> None:
    project_id, company_id = seed_company()
    del project_id
    factory = TrackingFactory()
    session = factory()
    try:
        lead = LeadRepository(session).create(company_id=company_id, contact_id=None)
        assert lead.id > 0
        assert lead.status == "NEW"
        assert session.commit_count == 1
        assert session.rollback_count == 0
    finally:
        session.close()
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Lead)) == 1


def test_lead_repository_pending_path_flushes_without_committing() -> None:
    _, company_id = seed_company()
    factory = TrackingFactory()
    session = factory()
    try:
        lead = LeadRepository(session).create_pending(company_id=company_id, contact_id=None)
        assert lead.id > 0
        assert session.commit_count == 0
        session.rollback()
    finally:
        session.close()
    with SessionLocal() as verification:
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0


def test_service_never_constructs_contact_or_provider_dependencies() -> None:
    project_id, company_id = seed_company()
    calls: list[str] = []

    def tracked(name: str, factory: Callable[[Session], object]) -> Callable[[Session], object]:
        def build(session: Session) -> object:
            calls.append(name)
            return factory(session)

        return build

    components = CompanyScopedOutreachCompletionComponents(
        project_repository=tracked("project", ProjectRepository),
        company_repository=tracked("company", CompanyRepository),
        enrichment_repository=tracked("enrichment", CompanyEnrichmentRepository),
        lead_repository=tracked("lead", LeadRepository),
        task_repository=tracked("task", TaskRepository),
    )

    result = complete(TrackingFactory(), project_id, company_id, components=components)

    assert result.contact_id is None
    assert calls == ["project", "company", "enrichment", "lead", "task"]
    assert counts() == (1, 1, 0)


def test_module_import_is_cli_provider_and_session_safe_in_fresh_process() -> None:
    script = """
import json
import sys
import app.modules.company.outreach_completion
forbidden = sorted(
    name for name in sys.modules
    if name.startswith('app.cli')
    or name.startswith('app.providers.openai')
    or name.startswith('app.providers.serpapi')
    or name.startswith('app.providers.smtp')
)
print(json.dumps(forbidden))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []

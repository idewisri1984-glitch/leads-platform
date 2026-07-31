import socket
import urllib.request
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

import app.cli.task as task_cli
from app.cli.main import app as root_app
from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.company_discovery.profile_execution import SearchProfileDiscoveryService
from app.modules.company_discovery.profile_persistence import (
    SearchProfileDiscoveryPersistenceService,
)
from app.modules.company_discovery.service import CompanyDiscoveryService
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryStagingService,
)
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task
from app.providers.serpapi.client import SerpApiClient

runner = CliRunner()
AS_OF = datetime(2026, 7, 31, 9)


@pytest.fixture
def engine():
    selected = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(selected, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    Base.metadata.create_all(selected)
    try:
        yield selected
    finally:
        selected.dispose()


def seed(engine: object) -> tuple[int, int]:
    with Session(engine) as session:  # type: ignore[arg-type]
        project = Project(name="Secret project")
        company = Company(project=project, name="Secret company")
        other = Company(project=project, name="Other company")
        lead = Lead(company=company, status="NEW", notes="secret lead notes")
        other_lead = Lead(company=other, status="NEW")
        session.add(project)
        session.flush()
        session.add_all(
            [
                Task(
                    lead=lead,
                    title="old\ncall",
                    description="secret description",
                    status="TODO",
                    due_at=AS_OF - timedelta(days=1),
                ),
                Task(
                    lead=lead,
                    title="now",
                    status="IN_PROGRESS",
                    due_at=AS_OF,
                ),
                Task(lead=lead, title="later", status="TODO", due_at=None),
                Task(lead=lead, title="done", status="DONE", due_at=None),
                Task(
                    lead=other_lead,
                    title="cross company",
                    status="TODO",
                    due_at=None,
                ),
            ]
        )
        session.commit()
        return company.id, other.id


def test_real_command_is_scoped_read_only_and_safe(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id, _ = seed(engine)
    monkeypatch.setattr(task_cli, "SessionLocal", lambda: Session(engine))
    result = runner.invoke(
        root_app,
        [
            "task",
            "queue",
            "--company-id",
            str(company_id),
            "--as-of",
            "2026-07-31T09:00:00",
        ],
    )
    assert result.exit_code == 0
    assert "Overdue: 1\nUpcoming: 1\nUnscheduled: 1\n" in result.output
    assert result.output.index("OVERDUE") < result.output.index("UPCOMING")
    assert result.output.index("UPCOMING") < result.output.index("UNSCHEDULED")
    assert '"old\\ncall"' in result.output
    for secret in (
        "secret description",
        "secret lead notes",
        "Secret company",
        "cross company",
        "done",
    ):
        assert secret not in result.output

    with Session(engine) as session:
        assert session.query(Task).count() == 5
        assert session.query(Task).filter(Task.status == "DONE").count() == 1


def test_missing_and_empty_company_output_match(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed(engine)
    with Session(engine) as session:  # type: ignore[arg-type]
        project = Project(name="Empty project")
        empty_company = Company(project=project, name="Empty company")
        session.add(project)
        session.commit()
        empty_company_id = empty_company.id
    monkeypatch.setattr(task_cli, "SessionLocal", lambda: Session(engine))
    outputs = []
    for company_id in (empty_company_id, 9999):
        selected = runner.invoke(
            root_app,
            [
                "task",
                "queue",
                "--company-id",
                str(company_id),
                "--as-of",
                "2026-07-31T09:00:00",
                "--days",
                "7",
            ],
        )
        assert selected.exit_code == 0
        outputs.append(selected.output.replace(f"Company ID: {company_id}", "Company ID"))
    assert outputs[0] == outputs[1]
    assert outputs[0].endswith("No active tasks in work queue.\n")


def test_real_queue_succeeds_with_all_runtime_boundary_guards(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id, _ = seed(engine)
    with Session(engine) as session:  # type: ignore[arg-type]
        company = session.get(Company, company_id)
        assert company is not None
        lead = session.query(Lead).filter(Lead.company_id == company_id).one()
        contact = Contact(
            company=company,
            first_name="Guarded",
            email="guarded@example.test",
            status="ACTIVE",
        )
        lead.contact = contact
        session.add(contact)
        session.commit()
        before_tasks = [
            (task.id, task.lead_id, task.title, task.description, task.status, task.due_at)
            for task in session.query(Task).order_by(Task.id)
        ]
        before_domain = (
            company.id,
            company.project_id,
            company.name,
            contact.id,
            contact.company_id,
            contact.first_name,
            lead.id,
            lead.company_id,
            lead.contact_id,
            lead.status,
            lead.source,
            lead.notes,
        )

    def forbidden(category: str):  # type: ignore[no-untyped-def]
        def fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(f"{category} boundary called")

        return fail

    monkeypatch.setattr(socket, "getaddrinfo", forbidden("dns"))
    monkeypatch.setattr(urllib.request, "urlopen", forbidden("urllib"))
    monkeypatch.setattr(SerpApiClient, "search_companies", forbidden("provider"))
    monkeypatch.setattr(SerpApiClient, "_parse_company_result", forbidden("parser"))
    monkeypatch.setattr(httpx.Client, "stream", forbidden("fetcher"))
    monkeypatch.setattr(SearchProfileDiscoveryService, "run_dry", forbidden("dry-run"))
    monkeypatch.setattr(
        SearchProfileDiscoveryPersistenceService,
        "run_persist",
        forbidden("persistence discovery"),
    )
    monkeypatch.setattr(
        CompanyDiscoveryService,
        "discover_from_serpapi",
        forbidden("company discovery"),
    )
    monkeypatch.setattr(CompanyDiscoveryStagingService, "run", forbidden("staging"))
    monkeypatch.setattr(task_cli, "SessionLocal", lambda: Session(engine))
    result = runner.invoke(
        root_app,
        [
            "task",
            "queue",
            "--company-id",
            str(company_id),
            "--as-of",
            "2026-07-31T09:00:00",
            "--days",
            "7",
        ],
    )
    assert result.exit_code == 0
    assert "Overdue: 1\nUpcoming: 1\nUnscheduled: 1\n" in result.output
    assert result.output.index("OVERDUE") < result.output.index("UPCOMING")
    assert result.output.index("UPCOMING") < result.output.index("UNSCHEDULED")
    assert 'Title: "old\\ncall"' in result.output
    with Session(engine) as session:  # type: ignore[arg-type]
        after_tasks = [
            (task.id, task.lead_id, task.title, task.description, task.status, task.due_at)
            for task in session.query(Task).order_by(Task.id)
        ]
        company = session.get(Company, company_id)
        contact = session.query(Contact).filter(Contact.company_id == company_id).one()
        lead = session.query(Lead).filter(Lead.company_id == company_id).one()
        assert company is not None
        after_domain = (
            company.id,
            company.project_id,
            company.name,
            contact.id,
            contact.company_id,
            contact.first_name,
            lead.id,
            lead.company_id,
            lead.contact_id,
            lead.status,
            lead.source,
            lead.notes,
        )
    assert after_tasks == before_tasks
    assert after_domain == before_domain

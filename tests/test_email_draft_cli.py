import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.cli import email_draft as cli
from app.cli.main import app
from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_draft.fake_provider import FakeEmailDraftGenerator
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

runner = CliRunner()


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine, expire_on_commit=False)
    yield value
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed(factory: sessionmaker[Session]) -> tuple[int, ...]:
    with factory() as session:
        project = Project(name="Project")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Company", status="NEW")
        session.add(company)
        session.flush()
        contact = Contact(
            company_id=company.id,
            first_name="Ada",
            email="ada@example.com",
            status="NEW",
        )
        session.add(contact)
        session.flush()
        lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW")
        session.add(lead)
        session.flush()
        task = Task(lead_id=lead.id, title="Outreach", status="TODO")
        session.add(task)
        session.commit()
        return project.id, company.id, contact.id, lead.id, task.id


def base(ids: tuple[int, ...]) -> list[str]:
    return [
        "--project-id",
        str(ids[0]),
        "--company-id",
        str(ids[1]),
        "--contact-id",
        str(ids[2]),
    ]


def test_help_registration_and_missing_confirmation_precede_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    help_result = runner.invoke(app, ["agent", "email-draft", "--help"])
    assert help_result.exit_code == 0
    for command in ("generate", "show", "approve", "reject"):
        assert command in help_result.stdout
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "forbidden"

    monkeypatch.setattr(cli, "execute_review", forbidden)
    result = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            "approve",
            "--project-id",
            "1",
            "--company-id",
            "2",
            "--contact-id",
            "3",
            "--draft-id",
            "4",
        ],
    )
    assert result.exit_code == 3
    assert result.stderr == "Email draft review requires --yes.\n"
    assert calls == 0


def test_public_offline_generate_show_approve_flow(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> None:
    ids = seed(factory)
    fake = FakeEmailDraftGenerator()
    original_generate = cli.execute_generate
    original_show = cli.execute_show
    original_review = cli.execute_review
    monkeypatch.setattr(
        cli,
        "execute_generate",
        lambda data, output: original_generate(
            data, output, session_factory=factory, generator_factory=lambda: fake
        ),
    )
    monkeypatch.setattr(
        cli,
        "execute_show",
        lambda data, output: original_show(data, output, session_factory=factory),
    )
    monkeypatch.setattr(
        cli,
        "execute_review",
        lambda data, action, output: original_review(data, action, output, session_factory=factory),
    )
    generate = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            "generate",
            *base(ids),
            "--lead-id",
            str(ids[3]),
            "--task-id",
            str(ids[4]),
            "--sender-name",
            "Alex",
            "--sender-company",
            "Bali Leads",
            "--purpose",
            "Discuss workflow",
            "--output",
            "json",
        ],
    )
    assert generate.exit_code == 0, generate.output
    payload = json.loads(generate.stdout)
    assert payload["status"] == "DRAFT"
    draft_id = payload["id"]
    show = runner.invoke(
        app,
        ["agent", "email-draft", "show", *base(ids), "--draft-id", str(draft_id)],
    )
    assert show.exit_code == 0 and 'status="DRAFT"' in show.stdout
    approve = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            "approve",
            *base(ids),
            "--draft-id",
            str(draft_id),
            "--yes",
            "--output",
            "json",
        ],
    )
    assert approve.exit_code == 0, approve.output
    assert json.loads(approve.stdout)["status"] == "APPROVED"
    assert len(fake.calls) == 1


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_duplicate_confirmation_and_unknown_options_are_sanitized(command: str) -> None:
    arguments = [
        "agent",
        "email-draft",
        command,
        "--project-id",
        "1",
        "--company-id",
        "2",
        "--contact-id",
        "3",
        "--draft-id",
        "4",
        "--yes",
        "--yes",
    ]
    duplicate = runner.invoke(app, arguments)
    assert duplicate.exit_code == 2 and duplicate.stderr == "Email draft data is invalid.\n"
    unknown = runner.invoke(app, arguments[:-2] + ["--yes", "--secret"])
    assert unknown.exit_code == 2 and unknown.stderr == "Email draft data is invalid.\n"

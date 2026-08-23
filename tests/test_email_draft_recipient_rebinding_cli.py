import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.cli import email_draft as cli
from app.cli.main import app
from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.email_draft.context import build_content_hash
from app.modules.email_draft.models import EmailDraft
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

runner = CliRunner()


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rebinding-cli.sqlite3'}")
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine, expire_on_commit=False)
    yield value
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed(factory: sessionmaker[Session]) -> tuple[dict[str, int], str]:
    with factory() as session:
        project = Project(name="Project")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Studio", website="https://example.com")
        session.add(company)
        session.flush()
        session.add(
            CompanyEnrichment(
                company_id=company.id, enrichment_status="SUCCEEDED", email="person@example.com"
            )
        )
        lead = Lead(company_id=company.id, contact_id=None, status="NEW")
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id, title="Outreach", description="Prepare outreach", status="TODO"
        )
        session.add(task)
        session.flush()
        subject = "A relevant collaboration"
        body = "Hello Studio team,\n\nA complete outreach body for review.\n\nRegards"
        content_hash = build_content_hash(
            recipient_email="person@example.com",
            subject=subject,
            text_body=body,
            prompt_version="email-outreach-draft-v1",
        )
        draft = EmailDraft(
            project_id=project.id,
            company_id=company.id,
            contact_id=None,
            lead_id=lead.id,
            task_id=task.id,
            recipient_email="person@example.com",
            recipient_name="Studio team",
            recipient_role=None,
            sender_name="Alex",
            sender_company="Bohemia Bali",
            generation_tone="warm",
            generation_purpose="Explore collaboration",
            generation_value_proposition=None,
            subject=subject,
            text_body=body,
            language="en",
            prompt_version="email-outreach-draft-v1",
            provider="fake",
            model="fake",
            context_fingerprint="a" * 64,
            request_fingerprint="b" * 64,
            content_hash=content_hash,
            status="DRAFT",
        )
        session.add(draft)
        session.commit()
        return {
            "project": project.id,
            "company": company.id,
            "lead": lead.id,
            "task": task.id,
            "draft": draft.id,
        }, content_hash


def arguments(ids: dict[str, int], content_hash: str, *, confirm: bool = True) -> list[str]:
    values = [
        "agent",
        "email-draft",
        "rebind-person-recipient",
        "--project-id",
        str(ids["project"]),
        "--company-id",
        str(ids["company"]),
        "--lead-id",
        str(ids["lead"]),
        "--task-id",
        str(ids["task"]),
        "--email-draft-id",
        str(ids["draft"]),
        "--recipient-email",
        "person@example.com",
        "--expected-content-hash",
        content_hash,
        "--first-name",
        "Yasmin",
        "--last-name",
        "Alsdais",
        "--job-title",
        "Interior Designer",
        "--country",
        "Saudi Arabia",
        "--city",
        "Riyadh",
        "--person-source-url",
        "https://example.com/about",
        "--location-source-url",
        "https://example.com/contact",
        "--output",
        "json",
    ]
    if confirm:
        values.append("--confirm")
    return values


def test_cli_help_success_and_exact_json(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    help_result = runner.invoke(app, ["agent", "email-draft", "rebind-person-recipient", "--help"])
    assert help_result.exit_code == 0
    ids, content_hash = seed(factory)
    original = cli.execute_rebind_person_recipient
    monkeypatch.setattr(
        cli,
        "execute_rebind_person_recipient",
        lambda data, output: original(data, output, session_factory=factory),
    )
    result = runner.invoke(app, arguments(ids, content_hash))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["contact_created"] is True and payload["changed"] is True
    assert payload["recipient_name_after"] == "Yasmin Alsdais"
    assert payload["network_call_count"] == payload["smtp_call_count"] == 0
    with factory() as session:
        assert session.scalar(select(Contact)).email == "person@example.com"


def test_cli_confirmation_duplicate_unknown_and_invalid_are_safe(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, content_hash = seed(factory)
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "forbidden"

    monkeypatch.setattr(cli, "execute_rebind_person_recipient", forbidden)
    missing = runner.invoke(app, arguments(ids, content_hash, confirm=False))
    assert (
        missing.exit_code == 3
        and missing.stderr == "Person recipient rebinding requires --confirm.\n"
    )
    duplicate = runner.invoke(app, arguments(ids, content_hash) + ["--confirm"])
    assert duplicate.exit_code == 2 and duplicate.stderr == "Email draft data is invalid.\n"
    unknown = runner.invoke(app, arguments(ids, content_hash) + ["--secret"])
    assert unknown.exit_code == 2 and unknown.stderr == "Email draft data is invalid.\n"
    invalid = runner.invoke(app, arguments(ids, "NOT_A_HASH"))
    assert invalid.exit_code == 2 and invalid.stderr == "Email draft data is invalid.\n"
    assert calls == 0
    with factory() as session:
        assert session.scalar(select(Contact)) is None


def test_cli_unknown_exception_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = {"project": 1, "company": 2, "lead": 3, "task": 4, "draft": 5}

    def explode(*args: object, **kwargs: object) -> str:
        raise RuntimeError("SECRET_DATABASE_VALUE")

    monkeypatch.setattr(cli, "execute_rebind_person_recipient", explode)
    result = runner.invoke(app, arguments(ids, "a" * 64))
    assert result.exit_code == 1
    assert result.stderr == "Person recipient rebinding failed.\n"
    assert "SECRET_DATABASE_VALUE" not in result.output

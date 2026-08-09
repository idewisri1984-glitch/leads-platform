import json
import smtplib
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
        company = Company(
            project_id=project.id,
            name="Pacific Design Atelier",
            city="Los Angeles",
            country="United States",
            industry="Interior Design",
            status="NEW",
        )
        session.add(company)
        session.flush()
        contact = Contact(
            company_id=company.id,
            first_name="Jordan",
            last_name="Lee",
            job_title="Purchasing Director",
            email="jordan@example.com",
            status="NEW",
        )
        session.add(contact)
        session.flush()
        lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW")
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id,
            title="Explore a responsible sourcing fit",
            description="Discuss whether the collection supports future interior sourcing needs.",
            status="TODO",
        )
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


@pytest.mark.parametrize(
    ("action", "expected_status", "timestamp_field", "other_timestamp"),
    [
        ("approve", "APPROVED", "approved_at", "rejected_at"),
        ("reject", "REJECTED", "rejected_at", "approved_at"),
    ],
)
def test_public_offline_generate_show_review_flow_has_canonical_timestamps_and_no_smtp(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    action: str,
    expected_status: str,
    timestamp_field: str,
    other_timestamp: str,
) -> None:
    ids = seed(factory)
    fake = FakeEmailDraftGenerator()

    def forbidden_smtp(*args: object, **kwargs: object) -> object:
        raise AssertionError("Email draft operations must not construct SMTP clients.")

    monkeypatch.setattr(smtplib, "SMTP", forbidden_smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden_smtp)
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
            "Maya",
            "--sender-company",
            "Bohemia Bali",
            "--purpose",
            "initial B2B outreach",
            "--value-proposition",
            "Handcrafted natural-stone and solid-wood furniture made in Bali for "
            "international interior projects.",
            "--output",
            "json",
        ],
    )
    assert generate.exit_code == 0, generate.output
    payload = json.loads(generate.stdout)
    assert payload["status"] == "DRAFT"
    assert payload["generated_at"].endswith("Z")
    assert "Jordan Lee" in payload["text_body"]
    assert "Purchasing Director" in payload["text_body"]
    assert "Pacific Design Atelier" in payload["text_body"]
    assert "Interior Design" in payload["text_body"]
    assert "Los Angeles, United States" in payload["text_body"]
    assert "Explore a responsible sourcing fit" in payload["text_body"]
    assert "supports future interior sourcing needs" in payload["text_body"]
    assert "Handcrafted natural-stone and solid-wood furniture" in payload["text_body"]
    draft_id = payload["id"]
    show = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            "show",
            *base(ids),
            "--draft-id",
            str(draft_id),
            "--output",
            "json",
        ],
    )
    assert show.exit_code == 0, show.output
    shown_payload = json.loads(show.stdout)
    for field in (
        "id",
        "recipient_email",
        "subject",
        "text_body",
        "prompt_version",
        "context_fingerprint",
        "content_hash",
        "status",
        "generated_at",
    ):
        assert shown_payload[field] == payload[field]
    review_result = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            action,
            *base(ids),
            "--draft-id",
            str(draft_id),
            "--yes",
            "--output",
            "json",
        ],
    )
    assert review_result.exit_code == 0, review_result.output
    reviewed_payload = json.loads(review_result.stdout)
    assert reviewed_payload["status"] == expected_status
    assert reviewed_payload[timestamp_field].endswith("Z")
    assert reviewed_payload["reviewed_at"].endswith("Z")
    assert reviewed_payload[other_timestamp] is None
    reviewed_show = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            "show",
            *base(ids),
            "--draft-id",
            str(draft_id),
            "--output",
            "json",
        ],
    )
    assert reviewed_show.exit_code == 0, reviewed_show.output
    reviewed_show_payload = json.loads(reviewed_show.stdout)
    assert reviewed_show_payload["generated_at"] == reviewed_payload["generated_at"]
    assert reviewed_show_payload["reviewed_at"] == reviewed_payload["reviewed_at"]
    assert reviewed_show_payload[timestamp_field] == reviewed_payload[timestamp_field]
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


def _generate_arguments(ids: tuple[int, ...]) -> list[str]:
    return [
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
    ]


def test_generate_unexpected_exception_is_sanitized_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> None:
    ids = seed(factory)
    counts = {"rollback": 0, "close": 0}
    database_session = factory()
    original_rollback = database_session.rollback
    original_close = database_session.close

    def rollback() -> None:
        counts["rollback"] += 1
        original_rollback()

    def close() -> None:
        counts["close"] += 1
        original_close()

    database_session.rollback = rollback
    database_session.close = close
    original_execute = cli.execute_generate

    def execute(data: object, output: str) -> str:
        def explode() -> object:
            raise RuntimeError("SECRET_PROVIDER_FAILURE")

        return original_execute(
            data,
            output,
            session_factory=lambda: database_session,
            generator_factory=explode,
        )

    monkeypatch.setattr(cli, "execute_generate", execute)
    result = runner.invoke(app, _generate_arguments(ids))
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Email draft operation failed.\n"
    assert "SECRET_PROVIDER_FAILURE" not in result.output
    assert counts == {"rollback": 1, "close": 1}
    with factory() as fresh:
        from app.modules.email_draft.models import EmailDraft

        assert fresh.query(EmailDraft).count() == 0


@pytest.mark.parametrize(
    ("command", "executor_name", "secret"),
    [
        ("show", "execute_show", "SECRET_DATABASE_FAILURE"),
        ("approve", "execute_review", "SECRET_APPROVAL_FAILURE"),
        ("reject", "execute_review", "SECRET_REJECTION_FAILURE"),
    ],
)
def test_other_public_commands_sanitize_unexpected_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    executor_name: str,
    secret: str,
) -> None:
    calls = 0

    def explode(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError(secret)

    monkeypatch.setattr(cli, executor_name, explode)
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
    ]
    if command in {"approve", "reject"}:
        arguments.append("--yes")
    result = runner.invoke(app, arguments)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Email draft operation failed.\n"
    assert secret not in result.output
    assert calls == 1

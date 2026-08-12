import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from app.cli.main import app
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_draft.models import EmailDraft
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

runner = CliRunner()


_SNAPSHOT_MODELS = (
    Project,
    Company,
    Contact,
    Lead,
    Task,
    EmailDraft,
    ManualEmailSendRecord,
)


def _serialized_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.isoformat()
    return value


def _database_snapshot() -> dict[str, tuple[tuple[tuple[str, object], ...], ...]]:
    from app.modules.email_delivery.models import EmailDeliveryAttempt

    models = (*_SNAPSHOT_MODELS, EmailDeliveryAttempt)
    with SessionLocal() as session:
        return {
            model.__tablename__: tuple(
                tuple(
                    (column.name, _serialized_value(getattr(record, column.name)))
                    for column in model.__table__.columns
                )
                for record in session.scalars(select(model).order_by(model.id)).all()
            )
            for model in models
        }


@contextmanager
def _reject_dml(engine: Engine) -> Iterator[list[str]]:
    mutations: list[str] = []

    def before_execute(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        first_word = statement.lstrip().split(None, 1)[0].upper()
        if first_word in {"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "REPLACE", "UPDATE"}:
            mutations.append(statement)
            raise AssertionError(f"CRM list attempted SQL mutation: {first_word}")

    event.listen(engine, "before_cursor_execute", before_execute)
    try:
        yield mutations
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)


def test_real_repository_filters_and_cli_are_read_only() -> None:
    from app.core.database.engine import engine

    with SessionLocal() as session:
        project = Project(name="CRM Project")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="CRM Company")
        session.add(company)
        session.flush()
        contact = Contact(
            company_id=company.id,
            first_name="Ada",
            last_name="Lovelace",
            job_title="Founder",
            email="ada@example.com",
            source="MANUAL_VERIFIED",
        )
        session.add(contact)
        session.flush()
        lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW")
        session.add(lead)
        session.flush()
        task = Task(lead_id=lead.id, title="Review", status="TODO")
        session.add(task)
        session.flush()
        draft = EmailDraft(
            project_id=project.id,
            company_id=company.id,
            contact_id=contact.id,
            lead_id=lead.id,
            task_id=task.id,
            recipient_email="ada@example.com",
            recipient_name="Ada Lovelace",
            recipient_role="Founder",
            sender_name="Operator",
            sender_company="CRM Operator",
            generation_tone="professional",
            generation_purpose="Introduce the company",
            generation_value_proposition=None,
            subject="Hello",
            text_body="Body",
            language="en",
            prompt_version="test-v1",
            provider="fake",
            model="fake",
            context_fingerprint="a" * 64,
            request_fingerprint="b" * 64,
            content_hash="c" * 64,
            status="APPROVED",
            delivery_mode="MANUAL",
            reviewed_at=datetime(2026, 8, 11, tzinfo=UTC),
            approved_at=datetime(2026, 8, 11, tzinfo=UTC),
        )
        session.add(draft)
        session.flush()
        session.add(
            ManualEmailSendRecord(
                project_id=project.id,
                company_id=company.id,
                contact_id=contact.id,
                email_draft_id=draft.id,
                recipient_email="ada@example.com",
                sent_at=datetime(2026, 8, 11, tzinfo=UTC),
            )
        )
        session.commit()
        project_id, company_id = project.id, company.id
    before = _database_snapshot()

    with _reject_dml(engine) as mutations:
        result = runner.invoke(
            app,
            ["crm", "list", "--project-id", str(project_id), "--company-id", str(company_id)],
        )
    assert result.exit_code == 0
    assert "CRM Company" in result.output
    assert "Ada Lovelace" in result.output
    assert "MANUALLY_SENT" in result.output
    assert mutations == []

    after = _database_snapshot()
    assert after == before


def test_database_snapshot_detects_update_without_count_change() -> None:
    before = {"tasks": ((("id", 1), ("status", "TODO")),)}
    after = {"tasks": ((("id", 1), ("status", "DONE")),)}
    assert before != after


def test_crm_help_is_fresh_process_import_safe() -> None:
    proof = r"""
import smtplib
import socket
import pydantic_settings
import sqlalchemy
import sqlalchemy.orm
from typer.testing import CliRunner

counts = {name: 0 for name in ('settings', 'engine', 'sessionmaker', 'session', 'smtp', 'network')}
def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail
pydantic_settings.BaseSettings.__init__ = blocked('settings')
sqlalchemy.create_engine = blocked('engine')
sqlalchemy.orm.sessionmaker = blocked('sessionmaker')
sqlalchemy.orm.Session.__init__ = blocked('session')
smtplib.SMTP = blocked('smtp')
smtplib.SMTP_SSL = blocked('smtp')
socket.create_connection = blocked('network')
socket.getaddrinfo = blocked('network')
from app.cli.main import app
results = [
    CliRunner().invoke(app, arguments).exit_code
    for arguments in (['--help'], ['crm', '--help'], ['crm', 'list', '--help'])
]
print(results)
print(counts)
"""
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    result = subprocess.run(
        [sys.executable, "-c", proof],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "[0, 0, 0]",
        "{'settings': 0, 'engine': 0, 'sessionmaker': 0, 'session': 0, 'smtp': 0, 'network': 0}",
    ]

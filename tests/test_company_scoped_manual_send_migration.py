import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_draft.models import EmailDraft
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

_ROOT = Path(__file__).resolve().parents[1]
_PREVIOUS = "d82f4c6a91b3"
_REVISION = "e41f7a9c2b60"


def _alembic(database: Path, *arguments: str, check: bool = True):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    environment["DEBUG"] = "false"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def _nullable(database: Path) -> bool:
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        columns = {
            item["name"]: item for item in inspect(engine).get_columns("manual_email_send_records")
        }
        return bool(columns["contact_id"]["nullable"])
    finally:
        engine.dispose()


def _revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def _insert_existing_person_record(database: Path) -> int:
    now = datetime(2026, 8, 18, 10, tzinfo=UTC)
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        with Session(engine) as session:
            project = Project(name="Existing manual outreach")
            session.add(project)
            session.flush()
            company = Company(project_id=project.id, name="Existing Company")
            session.add(company)
            session.flush()
            contact = Contact(
                company_id=company.id,
                first_name="Existing",
                email="existing@example.test",
            )
            session.add(contact)
            session.flush()
            lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW")
            session.add(lead)
            session.flush()
            task = Task(lead_id=lead.id, title="Existing task", status="TODO")
            session.add(task)
            session.flush()
            draft = EmailDraft(
                project_id=project.id,
                company_id=company.id,
                contact_id=contact.id,
                lead_id=lead.id,
                task_id=task.id,
                recipient_email="existing@example.test",
                recipient_name="Existing",
                recipient_role=None,
                sender_name="Operator",
                sender_company="Bohemia Bali",
                generation_tone="professional",
                generation_purpose="Outreach",
                generation_value_proposition=None,
                subject="Existing subject",
                text_body="Existing body",
                language="en",
                prompt_version="test",
                provider="fake",
                model="fake",
                context_fingerprint="a" * 64,
                request_fingerprint="b" * 64,
                content_hash="c" * 64,
                status="APPROVED",
                delivery_mode="MANUAL",
                generated_at=now,
                reviewed_at=now,
                approved_at=now,
            )
            session.add(draft)
            session.flush()
            record = ManualEmailSendRecord(
                project_id=project.id,
                company_id=company.id,
                contact_id=contact.id,
                email_draft_id=draft.id,
                recipient_email=draft.recipient_email,
                sent_at=now,
            )
            session.add(record)
            session.commit()
            return record.id
    finally:
        engine.dispose()


def _assert_person_record(database: Path, record_id: int) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT contact_id, recipient_email FROM manual_email_send_records WHERE id = ?",
            (record_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is not None
    assert row[1] == "existing@example.test"


def test_nullable_contact_upgrade_downgrade_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "company-manual-send.sqlite3"
    _alembic(database, "upgrade", _PREVIOUS)
    assert not _nullable(database)
    record_id = _insert_existing_person_record(database)
    _alembic(database, "upgrade", _REVISION)
    assert _nullable(database)
    _assert_person_record(database, record_id)
    _alembic(database, "downgrade", _PREVIOUS)
    assert not _nullable(database)
    _assert_person_record(database, record_id)
    _alembic(database, "upgrade", "head")
    assert _nullable(database)
    _assert_person_record(database, record_id)
    assert _revision(database) == _REVISION
    _alembic(database, "check")


def test_downgrade_blocks_company_scoped_records_before_ddl(tmp_path: Path) -> None:
    database = tmp_path / "company-manual-send-guard.sqlite3"
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO manual_email_send_records "
            "(id, project_id, company_id, contact_id, email_draft_id, "
            "recipient_email, sent_at, created_at) "
            "VALUES (1, 1, 1, NULL, 1, 'company@example.test', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()
    failed = _alembic(database, "downgrade", _PREVIOUS, check=False)
    assert failed.returncode != 0
    assert "Cannot downgrade e41f7a9c2b60" in failed.stderr
    assert _revision(database) == _REVISION
    assert _nullable(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT contact_id FROM manual_email_send_records WHERE id=1"
        ).fetchone() == (None,)


def test_model_metadata_matches_nullable_migration() -> None:
    assert Base.metadata.tables["manual_email_send_records"].c.contact_id.nullable

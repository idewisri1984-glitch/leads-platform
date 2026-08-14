import importlib.util
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, delete, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_draft.models import EmailDraft
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

_ROOT = Path(__file__).resolve().parents[1]
_REVISION = "9d6e7f8091a2"
_HEAD = "c71e3a9d4f20"
_HEAD_MIGRATION = (
    _ROOT / "alembic" / "versions" / "c71e3a9d4f20_allow_company_scoped_email_drafts.py"
)


def alembic(
    database: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
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


def revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def _load_head_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("c71e3a9d4f20_failure_test", _HEAD_MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_email_draft_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "email-draft.sqlite3"
    alembic(database, "upgrade", "8c5d6e7f8091")
    alembic(database, "upgrade", _REVISION)
    assert revision(database) == _REVISION
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info('email_drafts')")}
        assert {
            "recipient_email",
            "subject",
            "text_body",
            "prompt_version",
            "context_fingerprint",
            "content_hash",
            "status",
        } <= columns
        foreign_tables = {
            row[2] for row in connection.execute("PRAGMA foreign_key_list('email_drafts')")
        }
        assert foreign_tables == {"projects", "companies", "contacts", "leads", "tasks"}
    alembic(database, "downgrade", "8c5d6e7f8091")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='email_drafts'"
            ).fetchone()
            is None
        )
    alembic(database, "upgrade", "head")
    assert revision(database) == _HEAD
    alembic(database, "check")


def _seed_person_draft(database: Path) -> tuple[int, int, int, int, int, int]:
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        with Session(engine) as session:
            project = Project(name="Project")
            session.add(project)
            session.flush()
            company = Company(project_id=project.id, name="Company")
            session.add(company)
            session.flush()
            contact = Contact(
                company_id=company.id,
                first_name="Person",
                email="person@example.com",
            )
            session.add(contact)
            session.flush()
            lead = Lead(company_id=company.id, contact_id=contact.id)
            session.add(lead)
            session.flush()
            task = Task(lead_id=lead.id, title="Prepare outreach", status="TODO")
            session.add(task)
            session.flush()
            draft = _draft(
                project_id=project.id,
                company_id=company.id,
                contact_id=contact.id,
                lead_id=lead.id,
                task_id=task.id,
                marker="a",
            )
            session.add(draft)
            session.commit()
            return project.id, company.id, contact.id, lead.id, task.id, draft.id
    finally:
        engine.dispose()


def _draft(
    *,
    project_id: int,
    company_id: int,
    contact_id: int | None,
    lead_id: int,
    task_id: int,
    marker: str,
) -> EmailDraft:
    return EmailDraft(
        project_id=project_id,
        company_id=company_id,
        contact_id=contact_id,
        lead_id=lead_id,
        task_id=task_id,
        recipient_email=f"{marker}@example.com",
        recipient_name="Company team" if contact_id is None else "Person",
        recipient_role=None,
        sender_name="Sender",
        sender_company="Sender Company",
        generation_tone="professional",
        generation_purpose="Outreach",
        generation_value_proposition=None,
        subject="Subject",
        text_body="A sufficiently complete persisted email body for migration coverage.",
        language="en",
        prompt_version="email-outreach-draft-v1",
        provider="fake",
        model="fake",
        context_fingerprint=marker * 64,
        request_fingerprint=marker * 64,
        content_hash=marker * 64,
        status="DRAFT",
        generated_at=datetime(2026, 8, 13, tzinfo=UTC),
    )


def _seed_dependent_send_records(
    database: Path,
    *,
    project_id: int,
    company_id: int,
    contact_id: int,
    draft_id: int,
) -> tuple[int, int]:
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        with Session(engine) as session:
            manual_record = ManualEmailSendRecord(
                project_id=project_id,
                company_id=company_id,
                contact_id=contact_id,
                email_draft_id=draft_id,
                recipient_email="a@example.com",
                sent_at=datetime(2026, 8, 13, tzinfo=UTC),
            )
            delivery_attempt = EmailDeliveryAttempt(
                email_draft_id=draft_id,
                attempt_key="c" * 64,
                recipient_email="a@example.com",
                envelope_from="sender@example.com",
                header_from_email="sender@example.com",
                header_from_name="Sender",
                reply_to=None,
                message_id="<migration-test@example.com>",
                content_hash="a" * 64,
                transport_name="fake",
                security_mode="PLAINTEXT_LOCAL_TEST_ONLY",
            )
            session.add_all([manual_record, delivery_attempt])
            session.commit()
            return manual_record.id, delivery_attempt.id
    finally:
        engine.dispose()


def test_company_scoped_nullable_upgrade_and_guarded_downgrade(tmp_path: Path) -> None:
    database = tmp_path / "company-scoped-email-draft.sqlite3"
    alembic(database, "upgrade", "b52cd03e8f71")
    project_id, company_id, contact_id, lead_id, task_id, _draft_id = _seed_person_draft(database)
    alembic(database, "upgrade", _HEAD)
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        columns = {column["name"]: column for column in inspect(engine).get_columns("email_drafts")}
        assert columns["contact_id"]["nullable"] is True
        assert {
            fk["referred_table"] for fk in inspect(engine).get_foreign_keys("email_drafts")
        } == {
            "projects",
            "companies",
            "contacts",
            "leads",
            "tasks",
        }
        with Session(engine) as session:
            person = session.query(EmailDraft).filter_by(contact_id=contact_id).one()
            assert person.recipient_email == "a@example.com"
            session.add(
                _draft(
                    project_id=project_id,
                    company_id=company_id,
                    contact_id=None,
                    lead_id=lead_id,
                    task_id=task_id,
                    marker="b",
                )
            )
            session.commit()
    finally:
        engine.dispose()

    failed = alembic(database, "downgrade", "b52cd03e8f71", check=False)
    assert failed.returncode != 0
    assert "Cannot downgrade while company-scoped email drafts exist." in (
        failed.stdout + failed.stderr
    )

    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        with Session(engine) as session:
            session.execute(delete(EmailDraft).where(EmailDraft.contact_id.is_(None)))
            session.commit()
    finally:
        engine.dispose()

    alembic(database, "downgrade", "b52cd03e8f71")
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        columns = {column["name"]: column for column in inspect(engine).get_columns("email_drafts")}
        assert columns["contact_id"]["nullable"] is False
        assert "contacts" in {
            fk["referred_table"] for fk in inspect(engine).get_foreign_keys("email_drafts")
        }
    finally:
        engine.dispose()
    alembic(database, "upgrade", "head")
    assert revision(database) == _HEAD
    alembic(database, "check")


def test_populated_dependent_foreign_keys_survive_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "populated-dependent-email-draft.sqlite3"
    alembic(database, "upgrade", "b52cd03e8f71")
    project_id, company_id, contact_id, _lead_id, _task_id, draft_id = _seed_person_draft(database)
    manual_id, attempt_id = _seed_dependent_send_records(
        database,
        project_id=project_id,
        company_id=company_id,
        contact_id=contact_id,
        draft_id=draft_id,
    )

    def assert_preserved(*, nullable: bool) -> None:
        with sqlite3.connect(database) as connection:
            contact_column = next(
                row
                for row in connection.execute("PRAGMA table_info('email_drafts')")
                if row[1] == "contact_id"
            )
            assert bool(contact_column[3]) is not nullable
            assert connection.execute(
                "SELECT id, contact_id FROM email_drafts WHERE id = ?", (draft_id,)
            ).fetchone() == (draft_id, contact_id)
            assert connection.execute(
                "SELECT id, email_draft_id FROM manual_email_send_records WHERE id = ?",
                (manual_id,),
            ).fetchone() == (manual_id, draft_id)
            assert connection.execute(
                "SELECT id, email_draft_id FROM email_delivery_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone() == (attempt_id, draft_id)
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = '_alembic_tmp_email_drafts'"
                ).fetchone()
                is None
            )

    alembic(database, "upgrade", _HEAD)
    assert revision(database) == _HEAD
    assert_preserved(nullable=True)

    alembic(database, "downgrade", "b52cd03e8f71")
    assert revision(database) == "b52cd03e8f71"
    assert_preserved(nullable=False)

    alembic(database, "upgrade", _HEAD)
    assert revision(database) == _HEAD
    assert_preserved(nullable=True)


def test_sqlite_batch_failure_restores_fk_on_same_connection_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "failed-dependent-email-draft.sqlite3"
    alembic(database, "upgrade", "b52cd03e8f71")
    project_id, company_id, contact_id, _lead_id, _task_id, draft_id = _seed_person_draft(database)
    manual_id, attempt_id = _seed_dependent_send_records(
        database,
        project_id=project_id,
        company_id=company_id,
        contact_id=contact_id,
        draft_id=draft_id,
    )
    migration = _load_head_migration()
    captured_connections: list[Connection] = []
    fk_state_before_failure: list[int] = []

    class InjectedBatchFailure(RuntimeError):
        pass

    class FailingBatch:
        def __enter__(self) -> "FailingBatch":
            fk_state = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            fk_state_before_failure.append(fk_state)
            connection.exec_driver_sql(
                'CREATE TABLE "_alembic_tmp_email_drafts" (id INTEGER PRIMARY KEY)'
            )
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def alter_column(self, *args: object, **kwargs: object) -> None:
            raise InjectedBatchFailure("controlled batch recreation failure")

    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            connection.commit()
            dbapi_connection_id = id(connection.connection.driver_connection)
            migration_context = importlib.import_module("alembic.migration")
            operations = importlib.import_module("alembic.operations")
            context = migration_context.MigrationContext.configure(connection)
            real_get_bind = migration.op.get_bind

            def capture_get_bind() -> Connection:
                bound = real_get_bind()
                captured_connections.append(bound)
                return bound

            monkeypatch.setattr(migration.op, "get_bind", capture_get_bind)
            monkeypatch.setattr(
                migration.op,
                "batch_alter_table",
                lambda *args, **kwargs: FailingBatch(),
            )

            with (
                operations.Operations.context(context),
                context.begin_transaction(_per_migration=True),
                pytest.raises(
                    InjectedBatchFailure,
                    match="controlled batch recreation failure",
                ),
            ):
                migration.upgrade()

            assert captured_connections == [connection]
            assert fk_state_before_failure == [0]
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "b52cd03e8f71"
            )
            assert connection.exec_driver_sql(
                "SELECT id, contact_id, recipient_email FROM email_drafts WHERE id = ?",
                (draft_id,),
            ).one() == (draft_id, contact_id, "a@example.com")
            assert connection.exec_driver_sql(
                "SELECT id, email_draft_id FROM manual_email_send_records WHERE id = ?",
                (manual_id,),
            ).one() == (manual_id, draft_id)
            assert connection.exec_driver_sql(
                "SELECT id, email_draft_id FROM email_delivery_attempts WHERE id = ?",
                (attempt_id,),
            ).one() == (attempt_id, draft_id)
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = '_alembic_tmp_email_drafts'"
                ).first()
                is None
            )
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []

        with engine.connect() as reused_connection:
            assert id(reused_connection.connection.driver_connection) == dbapi_connection_id
            assert reused_connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()

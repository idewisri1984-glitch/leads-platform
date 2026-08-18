import json
import smtplib
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from typer.testing import CliRunner

from app.cli.main import app
from app.core.database import SessionLocal
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.manual_repository import ManualEmailSendRecordRepository
from app.modules.email_delivery.manual_schemas import (
    ConfirmedExternalManualEmailSendCommand,
    ManualOutreachStatus,
    ManualRecipientType,
)
from app.modules.email_delivery.manual_service import (
    ManualOutreachPersistenceError,
    ManualOutreachService,
    ManualOutreachStaleContextError,
)
from app.modules.email_draft.context import build_content_hash
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus, draft_is_sendable
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task, TaskLifecycleStatus

NOW = datetime(2026, 8, 18, 9, 30, tzinfo=UTC)


def _records(*, person_scoped: bool = False, enrichment_email: str | None = None) -> dict[str, int]:
    recipient = "recipient@example.test"
    with SessionLocal() as session:
        project = Project(name="External manual send")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Example Design Studio")
        session.add(company)
        session.flush()
        session.add(
            CompanyEnrichment(
                company_id=company.id,
                enrichment_status="SUCCEEDED",
                email=recipient if enrichment_email is None else enrichment_email,
                source_url="https://example.test/contact",
            )
        )
        contact = None
        if person_scoped:
            contact = Contact(
                company_id=company.id,
                first_name="Recipient",
                email=recipient,
                source="MANUAL_VERIFIED",
            )
            session.add(contact)
            session.flush()
        lead = Lead(
            company_id=company.id,
            contact_id=contact.id if contact is not None else None,
            status="NEW",
            source="MANUAL" if contact is not None else "COMPANY_SCOPED_OUTREACH",
        )
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id,
            title="Prepare personalized company outreach email",
            description="Prepare a personalized manual outreach email.",
            status=TaskLifecycleStatus.TODO.value,
        )
        session.add(task)
        session.flush()
        subject = "A relevant collaboration"
        body = "Hello,\n\nA concise, evidence-backed outreach message.\n\nBest regards"
        prompt_version = "email-outreach-draft-v1"
        draft = EmailDraft(
            project_id=project.id,
            company_id=company.id,
            contact_id=contact.id if contact is not None else None,
            lead_id=lead.id,
            task_id=task.id,
            recipient_email=recipient,
            recipient_name="Recipient" if contact is not None else company.name,
            recipient_role=None,
            sender_name="Operator",
            sender_company="Bohemia Bali",
            generation_tone="professional",
            generation_purpose="Outreach",
            generation_value_proposition=None,
            subject=subject,
            text_body=body,
            language="en",
            prompt_version=prompt_version,
            provider="fake",
            model="fake",
            context_fingerprint="a" * 64,
            request_fingerprint="b" * 64,
            content_hash=build_content_hash(
                recipient_email=recipient,
                subject=subject,
                text_body=body,
                prompt_version=prompt_version,
            ),
            status=EmailDraftStatus.DRAFT.value,
        )
        session.add(draft)
        session.commit()
        return {
            "project": project.id,
            "company": company.id,
            "contact": contact.id if contact is not None else 0,
            "lead": lead.id,
            "task": task.id,
            "draft": draft.id,
        }


def _command(ids: dict[str, int]) -> ConfirmedExternalManualEmailSendCommand:
    return ConfirmedExternalManualEmailSendCommand(
        project_id=ids["project"],
        company_id=ids["company"],
        email_draft_id=ids["draft"],
        confirmed=True,
    )


def _record(ids: dict[str, int]):
    with SessionLocal() as session:
        result = ManualOutreachService(
            session,
            ManualEmailSendRecordRepository(session),
            clock=lambda: NOW,
        ).record_external_manual_send(_command(ids))
        session.commit()
        return result


def test_company_scoped_external_record_is_atomic_and_creates_no_contact() -> None:
    ids = _records()
    result = _record(ids)
    assert result.outreach_status is ManualOutreachStatus.MANUALLY_SENT
    assert result.recipient_type is ManualRecipientType.COMPANY
    assert result.contact_id is None
    with SessionLocal() as session:
        draft = session.get(EmailDraft, ids["draft"])
        record = session.scalar(select(ManualEmailSendRecord))
        task = session.get(Task, ids["task"])
        lead = session.get(Lead, ids["lead"])
        assert draft is not None and draft.contact_id is None
        assert draft.status == EmailDraftStatus.DRAFT.value
        assert draft.reviewed_at is None
        assert draft.approved_at is None
        assert draft.delivery_mode == "MANUAL"
        assert record is not None and record.contact_id is None
        assert record.recipient_email == "recipient@example.test"
        assert task is not None and task.status == TaskLifecycleStatus.TODO.value
        assert lead is not None and lead.status == "NEW"
        assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_person_scoped_external_record_remains_supported() -> None:
    ids = _records(person_scoped=True)
    result = _record(ids)
    assert result.recipient_type is ManualRecipientType.PERSON
    assert result.contact_id == ids["contact"]
    with SessionLocal() as session:
        record = session.scalar(select(ManualEmailSendRecord))
        assert record is not None and record.contact_id == ids["contact"]


def test_external_record_is_idempotent_per_draft() -> None:
    ids = _records()
    first = _record(ids)
    second = _record(ids)
    assert second.manual_send_record_id == first.manual_send_record_id
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 1


@pytest.mark.parametrize("persisted", [None, "other@example.test"])
def test_missing_or_mismatched_canonical_recipient_is_rejected(persisted: str | None) -> None:
    ids = _records(enrichment_email=persisted or "recipient@example.test")
    if persisted is None:
        with SessionLocal() as session:
            enrichment = session.scalar(select(CompanyEnrichment))
            assert enrichment is not None
            enrichment.email = None
            session.commit()
    with SessionLocal() as session, pytest.raises(ManualOutreachStaleContextError):
        ManualOutreachService(
            session, ManualEmailSendRecordRepository(session), clock=lambda: NOW
        ).record_external_manual_send(_command(ids))
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0


def test_command_forbids_arbitrary_recipient_override() -> None:
    ids = _records()
    with pytest.raises(ValidationError):
        ConfirmedExternalManualEmailSendCommand(
            project_id=ids["project"],
            company_id=ids["company"],
            email_draft_id=ids["draft"],
            confirmed=True,
            recipient_email="attacker@example.test",
        )


def test_external_record_never_calls_smtp_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = _records()
    calls: list[str] = []

    def blocked(*_args: object, **_kwargs: object) -> None:
        calls.append("called")
        raise AssertionError("External recording attempted network delivery.")

    monkeypatch.setattr(smtplib, "SMTP", blocked)
    monkeypatch.setattr(smtplib, "SMTP_SSL", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    _record(ids)
    assert calls == []


def test_late_repository_failure_rolls_back_all_state() -> None:
    ids = _records()

    class FailingRepository(ManualEmailSendRecordRepository):
        def create(self, data):
            super().create(data)
            raise SQLAlchemyError("late failure")

    with SessionLocal() as session, pytest.raises(ManualOutreachPersistenceError):
        ManualOutreachService(
            session, FailingRepository(session), clock=lambda: NOW
        ).record_external_manual_send(_command(ids))
    with SessionLocal() as session:
        draft = session.get(EmailDraft, ids["draft"])
        task = session.get(Task, ids["task"])
        lead = session.get(Lead, ids["lead"])
        assert draft is not None and draft.status == EmailDraftStatus.DRAFT.value
        assert draft.delivery_mode is None
        assert task is not None and task.status == TaskLifecycleStatus.TODO.value
        assert lead is not None and lead.status == "NEW"
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0


def test_cli_records_company_scoped_send_without_contact_id() -> None:
    ids = _records()
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "email-draft",
            "record-manual-send",
            "--project-id",
            str(ids["project"]),
            "--company-id",
            str(ids["company"]),
            "--email-draft-id",
            str(ids["draft"]),
            "--confirm",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["recipient_type"] == "COMPANY"
    assert payload["contact_id"] is None
    assert payload["outreach_status"] == "MANUALLY_SENT"


def test_company_scoped_record_is_exported_as_sent(tmp_path: Path) -> None:
    ids = _records()
    _record(ids)
    destination = tmp_path / "company-manual-send.xlsx"
    result = CliRunner().invoke(
        app,
        [
            "crm",
            "export-excel",
            "--project-id",
            str(ids["project"]),
            "--output-file",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    workbook = load_workbook(destination)
    headers = {cell.value: cell.column for cell in workbook["Sales Leads"][1]}
    row = workbook["Sales Leads"][2]
    assert row[headers["Recipient Type"] - 1].value == "COMPANY"
    assert row[headers["Outreach Status"] - 1].value == "MANUALLY_SENT"
    assert row[headers["Outreach Readiness"] - 1].value == "SENT"
    assert row[headers["Decision Maker Name"] - 1].value is None


def test_company_scoped_draft_remains_ineligible_for_platform_delivery() -> None:
    ids = _records()
    _record(ids)
    with SessionLocal() as session:
        draft = session.get(EmailDraft, ids["draft"])
        assert draft is not None and not draft_is_sendable(draft)

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_draft.context import build_content_hash
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.modules.email_draft.recipient_rebinding import (
    PersonRecipientRebindingAlreadySentError,
    PersonRecipientRebindingConflictError,
    PersonRecipientRebindingInternalError,
    PersonRecipientRebindingInvalidDataError,
    PersonRecipientRebindingNotEligibleError,
    PersonRecipientRebindingService,
    PersonRecipientRebindingStaleContextError,
)
from app.modules.email_draft.recipient_rebinding_schemas import PersonRecipientRebindingInput
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

EMAIL = "yasmin@example.com"
SUBJECT = "Custom furniture for Example Studio"
BODY = "Hello Example Studio team,\n\nA concise, evidence-backed outreach message.\n\nRegards"
PROMPT = "email-outreach-draft-v1"


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rebinding.sqlite3'}")
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine, expire_on_commit=False)
    yield value
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed(factory: sessionmaker[Session]) -> dict[str, int | str]:
    with factory() as session:
        project = Project(name="Bohemia Bali")
        session.add(project)
        session.flush()
        company = Company(
            project_id=project.id, name="Example Studio", website="https://example.com"
        )
        session.add(company)
        session.flush()
        session.add(
            CompanyEnrichment(
                company_id=company.id,
                enrichment_status="SUCCEEDED",
                email=EMAIL,
                source_url="https://example.com",
            )
        )
        lead = Lead(
            company_id=company.id, contact_id=None, status="NEW", source="COMPANY_SCOPED_OUTREACH"
        )
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id, title="Prepare outreach", description="Manual outreach", status="TODO"
        )
        session.add(task)
        session.flush()
        content_hash = build_content_hash(
            recipient_email=EMAIL, subject=SUBJECT, text_body=BODY, prompt_version=PROMPT
        )
        draft = EmailDraft(
            project_id=project.id,
            company_id=company.id,
            contact_id=None,
            lead_id=lead.id,
            task_id=task.id,
            recipient_email=EMAIL,
            recipient_name="Example Studio team",
            recipient_role=None,
            sender_name="Alex",
            sender_company="Bohemia Bali",
            generation_tone="warm",
            generation_purpose="Explore collaboration",
            generation_value_proposition="Handcrafted furniture",
            subject=SUBJECT,
            text_body=BODY,
            language="en",
            prompt_version=PROMPT,
            provider="fake",
            model="fake",
            context_fingerprint="a" * 64,
            request_fingerprint="b" * 64,
            content_hash=content_hash,
            status=EmailDraftStatus.DRAFT.value,
        )
        session.add(draft)
        session.commit()
        return {
            "project": project.id,
            "company": company.id,
            "lead": lead.id,
            "task": task.id,
            "draft": draft.id,
            "hash": content_hash,
        }


def command(ids: dict[str, int | str], **changes: object) -> PersonRecipientRebindingInput:
    values: dict[str, object] = {
        "project_id": ids["project"],
        "company_id": ids["company"],
        "lead_id": ids["lead"],
        "task_id": ids["task"],
        "email_draft_id": ids["draft"],
        "recipient_email": EMAIL,
        "expected_content_hash": ids["hash"],
        "first_name": "Yasmin",
        "last_name": "Alsdais",
        "job_title": "Interior Designer",
        "country": "Saudi Arabia",
        "city": "Riyadh",
        "person_source_url": "https://example.com/about/yasmin",
        "location_source_url": "https://example.com/contact",
        "confirmed": True,
    }
    values.update(changes)
    return PersonRecipientRebindingInput(**values)


def test_atomic_first_rebinding_and_exact_rerun_are_idempotent(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    first = PersonRecipientRebindingService(factory).rebind(command(ids))
    second = PersonRecipientRebindingService(factory).rebind(command(ids))
    assert first.contact_created is True and first.contact_reused is False
    assert first.changed is True and second.changed is False
    assert second.contact_id == first.contact_id and second.contact_reused is True
    assert first.content_hash_before == first.content_hash_after == ids["hash"]
    assert first.context_fingerprint_after == first.request_fingerprint_after
    assert first.context_fingerprint_after != first.context_fingerprint_before
    assert first.network_call_count == first.smtp_call_count == 0
    with factory() as session:
        company = session.get(Company, ids["company"])
        contact = session.get(Contact, first.contact_id)
        lead = session.get(Lead, ids["lead"])
        draft = session.get(EmailDraft, ids["draft"])
        assert company is not None and (company.country, company.city) == ("Saudi Arabia", "Riyadh")
        assert contact is not None and contact.notes == "https://example.com/about/yasmin"
        assert contact.source == "OFFICIAL_WEBSITE"
        assert lead is not None and lead.contact_id == contact.id
        assert draft is not None and draft.contact_id == contact.id
        assert (draft.subject, draft.text_body, draft.content_hash) == (SUBJECT, BODY, ids["hash"])
        assert draft.recipient_name == "Yasmin Alsdais"
        assert draft.recipient_role == "Interior Designer"
        assert session.scalar(select(func.count()).select_from(Contact)) == 1
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0


def test_existing_matching_contact_is_reused(factory: sessionmaker[Session]) -> None:
    ids = seed(factory)
    with factory() as session:
        contact = Contact(
            company_id=ids["company"],
            first_name="Yasmin",
            last_name="Alsdais",
            job_title=None,
            email=EMAIL,
            status="NEW",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id
    result = PersonRecipientRebindingService(factory).rebind(command(ids))
    assert result.contact_id == contact_id and result.contact_reused is True
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Contact)) == 1
        contact = session.get(Contact, contact_id)
        assert contact is not None and contact.job_title == "Interior Designer"


def test_conflicting_contact_identity_rolls_back(factory: sessionmaker[Session]) -> None:
    ids = seed(factory)
    with factory() as session:
        session.add(
            Contact(company_id=ids["company"], first_name="Other", email=EMAIL, status="NEW")
        )
        session.commit()
    with pytest.raises(PersonRecipientRebindingConflictError):
        PersonRecipientRebindingService(factory).rebind(command(ids))
    assert_company_chain_unchanged(factory, ids, contact_count=1)


def test_late_failure_rolls_back_contact_location_lead_and_draft(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = seed(factory)
    service = PersonRecipientRebindingService(factory)
    monkeypatch.setattr(
        service, "_build_result", lambda **_: (_ for _ in ()).throw(RuntimeError("secret"))
    )
    with pytest.raises(PersonRecipientRebindingInternalError):
        service.rebind(command(ids))
    assert_company_chain_unchanged(factory, ids, contact_count=0)


@pytest.mark.parametrize("delivery_mode", ["MANUAL", "AUTOMATIC"])
def test_claimed_delivery_mode_blocks_rebinding(
    factory: sessionmaker[Session], delivery_mode: str
) -> None:
    ids = seed(factory)
    with factory() as session:
        draft = session.get(EmailDraft, ids["draft"])
        assert draft is not None
        draft.delivery_mode = delivery_mode
        session.commit()
    with pytest.raises(PersonRecipientRebindingAlreadySentError):
        PersonRecipientRebindingService(factory).rebind(command(ids))
    assert_company_chain_unchanged(factory, ids, contact_count=0, delivery_mode=delivery_mode)


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED"])
def test_reviewed_draft_blocks_rebinding(factory: sessionmaker[Session], status: str) -> None:
    ids = seed(factory)
    with factory() as session:
        draft = session.get(EmailDraft, ids["draft"])
        assert draft is not None
        draft.status = status
        draft.reviewed_at = datetime.now(UTC)
        if status == "APPROVED":
            draft.approved_at = datetime.now(UTC)
        else:
            draft.rejected_at = datetime.now(UTC)
        session.commit()
    with pytest.raises(PersonRecipientRebindingNotEligibleError):
        PersonRecipientRebindingService(factory).rebind(command(ids))


@pytest.mark.parametrize("field", ["subject", "text_body", "content_hash"])
def test_tampered_content_is_stale(factory: sessionmaker[Session], field: str) -> None:
    ids = seed(factory)
    with factory() as session:
        draft = session.get(EmailDraft, ids["draft"])
        assert draft is not None
        setattr(draft, field, "c" * 64 if field == "content_hash" else f"tampered {field}")
        session.commit()
    with pytest.raises(PersonRecipientRebindingStaleContextError):
        PersonRecipientRebindingService(factory).rebind(command(ids))


@pytest.mark.parametrize("target", ["request", "draft", "enrichment"])
def test_email_mismatch_fails_without_mutation(factory: sessionmaker[Session], target: str) -> None:
    ids = seed(factory)
    data = command(ids)
    if target == "request":
        data = command(ids, recipient_email="other@example.com")
    else:
        with factory() as session:
            if target == "draft":
                record = session.get(EmailDraft, ids["draft"])
                assert record is not None
            else:
                record = session.scalar(select(CompanyEnrichment))
                assert record is not None
            if target == "draft":
                record.recipient_email = "other@example.com"
            else:
                record.email = "other@example.com"
            session.commit()
    with pytest.raises(PersonRecipientRebindingConflictError):
        PersonRecipientRebindingService(factory).rebind(data)
    assert_company_chain_unchanged(
        factory,
        ids,
        contact_count=0,
        recipient_email=("other@example.com" if target == "draft" else EMAIL),
    )


@pytest.mark.parametrize(("field", "value"), [("country", "Indonesia"), ("city", "Ubud")])
def test_company_location_conflict_rolls_back(
    factory: sessionmaker[Session], field: str, value: str
) -> None:
    ids = seed(factory)
    with factory() as session:
        company = session.get(Company, ids["company"])
        assert company is not None
        setattr(company, field, value)
        session.commit()
    with pytest.raises(PersonRecipientRebindingConflictError):
        PersonRecipientRebindingService(factory).rebind(command(ids))
    with factory() as session:
        assert getattr(session.get(Company, ids["company"]), field) == value
        assert session.scalar(select(func.count()).select_from(Contact)) == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/person",
        "https://user:secret@example.com/person",
        "https://other.test/person",
        "not-a-url",
        "https://example.com/" + "x" * 500,
    ],
)
def test_source_url_validation_rejects_unsafe_values(
    factory: sessionmaker[Session], url: str
) -> None:
    ids = seed(factory)
    try:
        data = command(ids, person_source_url=url)
    except ValidationError:
        return
    with pytest.raises(PersonRecipientRebindingInvalidDataError):
        PersonRecipientRebindingService(factory).rebind(data)
    assert_company_chain_unchanged(factory, ids, contact_count=0)


def test_strict_input_rejects_bool_ids_extra_and_unconfirmed(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    values = command(ids).model_dump()
    for change in ({"project_id": True}, {"confirmed": False}, {"extra": "value"}):
        with pytest.raises(ValidationError):
            PersonRecipientRebindingInput(**(values | change))


def assert_company_chain_unchanged(
    factory: sessionmaker[Session],
    ids: dict[str, int | str],
    *,
    contact_count: int,
    delivery_mode: str | None = None,
    recipient_email: str = EMAIL,
) -> None:
    with factory() as session:
        company = session.get(Company, ids["company"])
        lead = session.get(Lead, ids["lead"])
        draft = session.get(EmailDraft, ids["draft"])
        assert company is not None and company.country is None and company.city is None
        assert lead is not None and lead.contact_id is None
        assert draft is not None and draft.contact_id is None
        assert draft.recipient_email == recipient_email and draft.delivery_mode == delivery_mode
        assert draft.context_fingerprint == "a" * 64 and draft.request_fingerprint == "b" * 64
        assert session.scalar(select(func.count()).select_from(Contact)) == contact_count

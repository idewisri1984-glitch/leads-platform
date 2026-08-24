from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_draft.context import (
    EmailDraftSourceRecords,
    build_content_hash,
    build_context_fingerprint,
    build_email_personalization_context,
)
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
from app.modules.email_draft.schemas import EmailDraftGenerationInput, EmailLanguage, EmailTone
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


def seed(
    factory: sessionmaker[Session], *, differing_company_id: bool = False
) -> dict[str, int | str]:
    with factory() as session:
        project = Project(name="Bohemia Bali")
        session.add(project)
        session.flush()
        if differing_company_id:
            session.add(
                Company(
                    project_id=project.id,
                    name="Decoy Studio",
                    website="https://decoy.example",
                )
            )
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


class TrackingSession(Session):
    commit_calls = 0
    rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()


class FlushIntegritySession(TrackingSession):
    def flush(self, objects: Sequence[object] | None = None) -> None:
        if self.new:
            raise IntegrityError(
                "INSERT INTO contacts VALUES (SECRET_VALUE)",
                {"recipient": "SECRET_VALUE"},
                RuntimeError("SECRET_VALUE"),
            )
        super().flush(objects)


class CommitIntegritySession(TrackingSession):
    def commit(self) -> None:
        self.commit_calls += 1
        raise IntegrityError(
            "UPDATE email_drafts SET request_fingerprint=SECRET_VALUE",
            {"fingerprint": "SECRET_VALUE"},
            RuntimeError("SECRET_VALUE"),
        )


def capturing_factory(
    factory: sessionmaker[Session], session_type: type[TrackingSession]
) -> tuple[object, list[TrackingSession]]:
    sessions: list[TrackingSession] = []

    def create() -> Session:
        session = session_type(bind=factory.kw["bind"], expire_on_commit=False)
        sessions.append(session)
        return session

    return create, sessions


def test_rebinding_uses_project_id_for_project_scope_when_company_id_differs(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory, differing_company_id=True)
    service_factory, sessions = capturing_factory(factory, TrackingSession)
    result = PersonRecipientRebindingService(service_factory).rebind(command(ids))
    assert ids["project"] == 1 and ids["company"] == 2
    assert result.company_id == 2 and result.contact_created is True
    assert len(sessions) == 1 and sessions[0].commit_calls == 1
    with factory() as session:
        company = session.get(Company, ids["company"])
        lead = session.get(Lead, ids["lead"])
        draft = session.get(EmailDraft, ids["draft"])
        assert company is not None and company.project_id == ids["project"]
        assert lead is not None and lead.contact_id == result.contact_id
        assert draft is not None and draft.contact_id == result.contact_id
        assert session.scalar(select(func.count()).select_from(Contact)) == 1
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 0
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 0


def test_flush_integrity_error_maps_to_conflict_and_rolls_back(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    service_factory, sessions = capturing_factory(factory, FlushIntegritySession)
    with pytest.raises(PersonRecipientRebindingConflictError):
        PersonRecipientRebindingService(service_factory).rebind(command(ids))
    assert len(sessions) == 1 and sessions[0].rollback_calls == 1
    assert_company_chain_unchanged(factory, ids, contact_count=0)


def test_commit_integrity_error_maps_to_conflict_and_rolls_back(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    service_factory, sessions = capturing_factory(factory, CommitIntegritySession)
    with pytest.raises(PersonRecipientRebindingConflictError):
        PersonRecipientRebindingService(service_factory).rebind(command(ids))
    assert len(sessions) == 1
    assert sessions[0].commit_calls == 1 and sessions[0].rollback_calls == 1
    assert_company_chain_unchanged(factory, ids, contact_count=0)


def test_real_manual_send_record_blocks_rebinding_without_mutation(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    with factory() as session:
        session.add(
            ManualEmailSendRecord(
                project_id=ids["project"],
                company_id=ids["company"],
                contact_id=None,
                email_draft_id=ids["draft"],
                recipient_email=EMAIL,
                sent_at=datetime.now(UTC),
            )
        )
        session.commit()
    with pytest.raises(PersonRecipientRebindingAlreadySentError):
        PersonRecipientRebindingService(factory).rebind(command(ids))
    assert_company_chain_unchanged(factory, ids, contact_count=0)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 1


def test_real_email_delivery_attempt_blocks_rebinding_without_mutation(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    with factory() as session:
        session.add(
            EmailDeliveryAttempt(
                email_draft_id=ids["draft"],
                attempt_key="c" * 64,
                outcome="RESERVED",
                recipient_email=EMAIL,
                envelope_from="sender@example.com",
                header_from_email="sender@example.com",
                header_from_name="Alex",
                reply_to=None,
                message_id="<rebinding-test@example.com>",
                content_hash=ids["hash"],
                transport_name="fake",
                security_mode="PLAINTEXT_LOCAL_TEST_ONLY",
            )
        )
        session.commit()
    with pytest.raises(PersonRecipientRebindingAlreadySentError):
        PersonRecipientRebindingService(factory).rebind(command(ids))
    assert_company_chain_unchanged(factory, ids, contact_count=0)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1


def test_final_request_fingerprint_collision_raises_conflict_and_rolls_back(
    factory: sessionmaker[Session],
) -> None:
    ids = seed(factory)
    with factory() as session:
        project = session.get(Project, ids["project"])
        company = session.get(Company, ids["company"])
        enrichment = session.scalar(select(CompanyEnrichment))
        lead = session.get(Lead, ids["lead"])
        task = session.get(Task, ids["task"])
        target = session.get(EmailDraft, ids["draft"])
        assert all(
            record is not None for record in (project, company, enrichment, lead, task, target)
        )
        assert project is not None and company is not None and enrichment is not None
        assert lead is not None and task is not None and target is not None
        contact = Contact(
            company_id=company.id,
            first_name="Yasmin",
            last_name="Alsdais",
            job_title="Interior Designer",
            email=EMAIL,
            country="Saudi Arabia",
            city="Riyadh",
            source="OFFICIAL_WEBSITE",
            status="NEW",
        )
        session.add(contact)
        session.flush()
        company.country = "Saudi Arabia"
        company.city = "Riyadh"
        lead.contact_id = contact.id
        generation = EmailDraftGenerationInput(
            project_id=project.id,
            company_id=company.id,
            contact_id=contact.id,
            lead_id=lead.id,
            task_id=task.id,
            sender_name=target.sender_name,
            sender_company=target.sender_company,
            language=EmailLanguage(target.language),
            tone=EmailTone(target.generation_tone),
            purpose=target.generation_purpose,
            value_proposition=target.generation_value_proposition,
            prompt_version=target.prompt_version,
        )
        context = build_email_personalization_context(
            EmailDraftSourceRecords(
                project=project,
                company=company,
                contact=contact,
                lead=lead,
                task=task,
                company_email=enrichment.email,
            )
        )
        fingerprint = build_context_fingerprint(context, generation)
        company.country = None
        company.city = None
        lead.contact_id = None
        collision_subject = "Existing person-scoped draft"
        collision_body = "A separate persisted draft that owns the final request fingerprint."
        collision_hash = build_content_hash(
            recipient_email=EMAIL,
            subject=collision_subject,
            text_body=collision_body,
            prompt_version=PROMPT,
        )
        collision = EmailDraft(
            project_id=project.id,
            company_id=company.id,
            contact_id=contact.id,
            lead_id=lead.id,
            task_id=task.id,
            recipient_email=EMAIL,
            recipient_name="Yasmin Alsdais",
            recipient_role="Interior Designer",
            sender_name="Alex",
            sender_company="Bohemia Bali",
            generation_tone="warm",
            generation_purpose="Explore collaboration",
            generation_value_proposition="Handcrafted furniture",
            subject=collision_subject,
            text_body=collision_body,
            language="en",
            prompt_version=PROMPT,
            provider="fake",
            model="fake",
            context_fingerprint=fingerprint,
            request_fingerprint=fingerprint,
            content_hash=collision_hash,
            status=EmailDraftStatus.DRAFT.value,
        )
        session.add(collision)
        session.commit()
        collision_id = collision.id
    with pytest.raises(PersonRecipientRebindingConflictError):
        PersonRecipientRebindingService(factory).rebind(command(ids))
    assert_company_chain_unchanged(factory, ids, contact_count=1)
    with factory() as session:
        collision = session.get(EmailDraft, collision_id)
        assert collision is not None and collision.request_fingerprint == fingerprint


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

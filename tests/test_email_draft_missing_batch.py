import smtplib
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.cli import email_draft as cli
from app.cli.main import app
from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.email_draft.batch import (
    MissingDraftBatchOptions,
    MissingDraftResultStatus,
    MissingEmailDraftBatchService,
)
from app.modules.email_draft.fake_provider import FakeEmailDraftGenerator
from app.modules.email_draft.models import EmailDraft, draft_is_sendable
from app.modules.email_draft.provider_interfaces import EmailDraftProviderUnavailableError
from app.modules.email_draft.repository import EmailDraftRepository
from app.modules.email_draft.schemas import (
    EmailDraftGenerationInput,
    EmailDraftReviewInput,
    EmailDraftScopeInput,
    EmailLanguage,
    EmailTone,
)
from app.modules.email_draft.service import EmailDraftService
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine, expire_on_commit=False)
    yield value
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed(
    factory: sessionmaker[Session],
    *,
    contact_email: str | None,
    company_email: str | None,
    companies: int = 1,
) -> int:
    with factory() as session:
        project = Project(name="Project")
        session.add(project)
        session.flush()
        for number in range(companies):
            company = Company(project_id=project.id, name=f"Studio {number}")
            session.add(company)
            session.flush()
            contact = Contact(
                company_id=company.id,
                first_name="Jordan",
                last_name="Lee",
                job_title="Founder",
                email=contact_email,
            )
            session.add(contact)
            session.flush()
            lead = Lead(company_id=company.id, contact_id=contact.id)
            session.add(lead)
            session.flush()
            session.add(Task(lead_id=lead.id, title="Prepare outreach", status="TODO"))
            if company_email is not None:
                session.add(CompanyEnrichment(company_id=company.id, email=company_email))
        session.commit()
        return project.id


def options(project_id: int, **values: object) -> MissingDraftBatchOptions:
    defaults: dict[str, object] = {
        "project_id": project_id,
        "limit": 20,
        "sender_name": "Alex",
        "sender_company": "Bohemia Bali",
        "purpose": "initial outreach",
    }
    defaults.update(values)
    return MissingDraftBatchOptions(
        project_id=int(defaults["project_id"]),
        limit=int(defaults["limit"]),
        sender_name=str(defaults["sender_name"]),
        sender_company=str(defaults["sender_company"]),
        purpose=str(defaults["purpose"]),
        value_proposition=None,
        dry_run=bool(defaults.get("dry_run", False)),
    )


def service(
    factory: sessionmaker[Session], fake: FakeEmailDraftGenerator
) -> MissingEmailDraftBatchService:
    return MissingEmailDraftBatchService(session_factory=factory, generator_factory=lambda: fake)


def test_person_batch_generates_without_confirmation_and_rerun_skips_before_ai(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = seed(factory, contact_email="jordan@example.com", company_email="team@example.com")
    fake = FakeEmailDraftGenerator()
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: pytest.fail("SMTP called"))
    first = service(factory, fake).run(options(project_id))
    second = service(factory, fake).run(options(project_id))
    assert first.ai_call_count == 1
    assert first.items[0].result is MissingDraftResultStatus.CREATED
    assert first.items[0].recipient_type == "DECISION_MAKER"
    assert second.ai_call_count == 0
    assert second.items[0].result is MissingDraftResultStatus.SKIPPED_EXISTING
    assert len(fake.calls) == 1
    with factory() as session:
        draft = session.scalar(select(EmailDraft))
        assert draft is not None and draft.contact_id is not None
        assert session.scalar(select(func.count()).select_from(EmailDraft)) == 1


def test_company_email_creates_truthful_company_scoped_draft(
    factory: sessionmaker[Session],
) -> None:
    project_id = seed(factory, contact_email=None, company_email="hello@studio.example")
    fake = FakeEmailDraftGenerator()
    result = service(factory, fake).run(options(project_id))
    assert result.items[0].recipient_type == "COMPANY"
    assert result.items[0].contact_id is None
    with factory() as session:
        draft = session.scalar(select(EmailDraft))
        assert draft is not None
        assert draft.contact_id is None
        assert draft.recipient_email == "hello@studio.example"
        assert draft.recipient_name == "Studio 0 team"
    assert fake.calls[0].context.recipient_name == "Studio 0 team"


@pytest.mark.parametrize("contact_id", [None, 1])
def test_generation_schema_accepts_null_or_positive_contact_id(contact_id: int | None) -> None:
    data = EmailDraftGenerationInput(
        project_id=1,
        company_id=1,
        contact_id=contact_id,
        lead_id=1,
        task_id=1,
        sender_name="Alex",
        sender_company="Bohemia Bali",
        language=EmailLanguage.EN,
        tone=EmailTone.PROFESSIONAL,
        purpose="initial outreach",
        prompt_version="email-outreach-draft-v1",
    )
    assert data.contact_id == contact_id


@pytest.mark.parametrize("contact_id", [0, -1, "1"])
def test_generation_schema_rejects_invalid_contact_id(contact_id: object) -> None:
    with pytest.raises(ValueError):
        EmailDraftGenerationInput(
            project_id=1,
            company_id=1,
            contact_id=contact_id,
            lead_id=1,
            task_id=1,
            sender_name="Alex",
            sender_company="Bohemia Bali",
            language=EmailLanguage.EN,
            tone=EmailTone.PROFESSIONAL,
            purpose="initial outreach",
            prompt_version="email-outreach-draft-v1",
        )


def test_company_draft_read_and_approval_preserve_null_contact(
    factory: sessionmaker[Session],
) -> None:
    project_id = seed(factory, contact_email=None, company_email="hello@studio.example")
    fake = FakeEmailDraftGenerator()
    created = service(factory, fake).run(options(project_id)).items[0]
    assert created.draft_id is not None
    with factory() as session:
        draft = session.get(EmailDraft, created.draft_id)
        assert draft is not None and not draft_is_sendable(draft)
        draft_service = EmailDraftService(
            session=session,
            repository=EmailDraftRepository(session),
            generator=None,
        )
        shown = draft_service.show(
            EmailDraftScopeInput(
                project_id=project_id,
                company_id=draft.company_id,
                contact_id=None,
                draft_id=draft.id,
            )
        )
        approved = draft_service.approve(
            EmailDraftReviewInput(
                project_id=project_id,
                company_id=draft.company_id,
                contact_id=None,
                draft_id=draft.id,
                confirmed=True,
            )
        )
        session.commit()
    assert shown.contact_id is None
    assert approved.contact_id is None
    assert approved.status == "APPROVED"


def test_invalid_email_and_dry_run_skip_ai_and_mutation(
    factory: sessionmaker[Session],
) -> None:
    project_id = seed(factory, contact_email="invalid", company_email=None)
    fake = FakeEmailDraftGenerator()
    skipped = service(factory, fake).run(options(project_id))
    assert skipped.items[0].result is MissingDraftResultStatus.SKIPPED_NO_EMAIL
    assert skipped.ai_call_count == 0
    project_two = seed(factory, contact_email="valid@example.com", company_email=None)
    dry = service(factory, fake).run(options(project_two, dry_run=True))
    assert dry.items[0].result is MissingDraftResultStatus.WOULD_CREATE
    assert dry.ai_call_count == 0
    assert fake.calls == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EmailDraft)) == 0


def test_limit_is_deterministic_and_provider_failure_is_isolated(
    factory: sessionmaker[Session],
) -> None:
    project_id = seed(
        factory,
        contact_email="valid@example.com",
        company_email=None,
        companies=3,
    )
    limited_fake = FakeEmailDraftGenerator()
    limited = service(factory, limited_fake).run(options(project_id, limit=2))
    assert [item.company_id for item in limited.items] == sorted(
        item.company_id for item in limited.items
    )
    assert len(limited.items) == 2
    assert limited.ai_call_count == 2


def test_provider_failure_isolated_and_counted(factory: sessionmaker[Session]) -> None:
    project_id = seed(
        factory,
        contact_email="valid@example.com",
        company_email=None,
        companies=2,
    )
    fake = FakeEmailDraftGenerator()

    class FailOnceGenerator:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                raise EmailDraftProviderUnavailableError("unavailable")
            return fake.generate(request)

    provider = FailOnceGenerator()
    result = MissingEmailDraftBatchService(
        session_factory=factory,
        generator_factory=lambda: provider,
    ).run(options(project_id))
    assert [item.result for item in result.items] == [
        MissingDraftResultStatus.FAILED,
        MissingDraftResultStatus.CREATED,
    ]
    assert result.ai_call_count == 2
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EmailDraft)) == 1


def test_generate_missing_cli_requires_no_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def execute(**kwargs: object) -> str:
        nonlocal calls
        calls += 1
        assert "confirmed" not in kwargs
        return "batch-ok"

    monkeypatch.setattr(cli, "execute_generate_missing", execute)
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "email-draft",
            "generate-missing",
            "--project-id",
            "1",
            "--sender-name",
            "Alex",
            "--sender-company",
            "Bohemia Bali",
            "--purpose",
            "initial outreach",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout == "batch-ok\n"
    assert calls == 1

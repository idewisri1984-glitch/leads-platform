from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.database.session import SessionLocal
from app.modules.agent.lead_acquisition import LeadAcquisitionInput
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment, EnrichmentStatus
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_draft.fake_provider import FakeEmailDraftGenerator
from app.modules.email_draft.models import EmailDraft
from app.modules.email_draft.provider_interfaces import EmailDraftProviderUnavailableError
from app.modules.lead.models import Lead
from app.modules.lead_acquisition_execution import execute_lead_acquisition
from app.modules.project.models import Project
from app.modules.task.models import Task


def _seed_company(*, company_email: str | None = "office@studio.example") -> tuple[int, int]:
    with SessionLocal() as session:
        project = Project(name="Stage 6A Integration")
        session.add(project)
        session.flush()
        company = Company(
            project_id=project.id,
            name="Atelier Example",
            website="https://studio.example",
            industry="Interior Design",
        )
        session.add(company)
        session.flush()
        if company_email is not None:
            session.add(
                CompanyEnrichment(
                    company_id=company.id,
                    enrichment_status=EnrichmentStatus.SUCCEEDED,
                    email=company_email,
                )
            )
        session.commit()
        return project.id, company.id


def _seed_person(company_id: int) -> tuple[int, int, int]:
    with SessionLocal() as session:
        contact = Contact(
            company_id=company_id,
            first_name="Ada",
            last_name="Meyer",
            job_title="Founder",
            email="ada@studio.example",
            status="NEW",
        )
        session.add(contact)
        session.flush()
        lead = Lead(company_id=company_id, contact_id=contact.id, status="NEW", source="AGENT")
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id,
            title="Prepare personalized manual outreach email",
            description="Use verified company and contact context only.",
            status="TODO",
        )
        session.add(task)
        session.commit()
        return contact.id, lead.id, task.id


def _seed_company_outreach(company_id: int) -> tuple[int, int]:
    with SessionLocal() as session:
        lead = Lead(company_id=company_id, contact_id=None, status="NEW", source="AGENT")
        session.add(lead)
        session.flush()
        task = Task(
            lead_id=lead.id,
            title="Prepare personalized company outreach email",
            description="Prepare a personalized manual outreach email for the company recipient.",
            status="TODO",
        )
        session.add(task)
        session.commit()
        return lead.id, task.id


def _seed_additional_company(project_id: int) -> int:
    with SessionLocal() as session:
        company = Company(
            project_id=project_id,
            name="Second Atelier",
            website="https://second.example",
            industry="Hospitality Design",
        )
        session.add(company)
        session.flush()
        session.add(
            CompanyEnrichment(
                company_id=company.id,
                enrichment_status=EnrichmentStatus.SUCCEEDED,
                email="office@second.example",
            )
        )
        session.commit()
        return company.id


def _install_boundaries(
    monkeypatch,
    *,
    company_id: int,
    person: tuple[int, int, int] | None = None,
) -> None:
    monkeypatch.setattr(
        "app.modules.agent.execution.execute_company_plan",
        lambda *args, **kwargs: SimpleNamespace(
            discovery_run_id=101,
            staged_candidate_count=1,
            selected_candidate_id=201,
            serpapi_call_count=0,
            openai_call_count=0,
        ),
    )
    monkeypatch.setattr(
        "app.modules.agent.execution.execute_company_apply",
        lambda *args, **kwargs: SimpleNamespace(
            company_id=company_id,
            company_created=False,
            company_reused=True,
        ),
    )
    if person is None:
        contact_result = SimpleNamespace(
            selected_candidate_id=None,
            selected_contact_email=None,
            handoff_token=None,
            provider_call_count=0,
            decision=SimpleNamespace(value="NO_SELECTION"),
        )
    else:
        contact_result = SimpleNamespace(
            selected_candidate_id=301,
            selected_contact_email="ada@studio.example",
            handoff_token="a" * 64,
            provider_call_count=0,
            decision=SimpleNamespace(value="SELECT"),
        )
        contact_id, lead_id, task_id = person
        monkeypatch.setattr(
            "app.modules.agent.execution.execute_contact_apply",
            lambda *args, **kwargs: SimpleNamespace(
                contact_id=contact_id,
                lead_id=lead_id,
                task_id=task_id,
                contact_created=False,
                contact_reused=True,
                lead_created=False,
                lead_reused=True,
                task_created=False,
                task_reused=True,
            ),
        )
    monkeypatch.setattr(
        "app.modules.agent.execution.execute_contact_plan",
        lambda *args, **kwargs: contact_result,
    )


def _run(project_id: int, generator_factory, *, limit: int = 1) -> object:
    return execute_lead_acquisition(
        LeadAcquisitionInput(
            project_id=project_id,
            search_profile_id=1,
            limit=limit,
            goal="Find relevant interior design firms",
        ),
        session_factory=SessionLocal,
        decision_factory_factory=lambda: object(),
        serpapi_client_factory=lambda **kwargs: object(),
        contact_provider_factory=lambda: object(),
        email_generator_factory=generator_factory,
    )


def _counts() -> tuple[int, int, int, int, int, int]:
    with SessionLocal() as session:
        return (
            session.scalar(select(func.count()).select_from(Contact)) or 0,
            session.scalar(select(func.count()).select_from(Lead)) or 0,
            session.scalar(select(func.count()).select_from(Task)) or 0,
            session.scalar(select(func.count()).select_from(EmailDraft)) or 0,
            session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) or 0,
            session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) or 0,
        )


def test_company_scoped_completion_persists_and_restart_reuses_without_send(monkeypatch) -> None:
    project_id, company_id = _seed_company()
    _install_boundaries(monkeypatch, company_id=company_id)
    generator = FakeEmailDraftGenerator()

    first = _run(project_id, lambda: generator)
    first_counts = _counts()

    assert first.completed_count == 1
    assert (first.leads_created, first.tasks_created, first.drafts_created) == (1, 1, 1)
    assert first_counts == (0, 1, 1, 1, 0, 0)
    with SessionLocal() as session:
        draft = session.get(EmailDraft, first.completed_draft_ids[0])
        lead = session.get(Lead, first.completed_lead_ids[0])
        task = session.get(Task, first.completed_task_ids[0])
        assert draft is not None and draft.contact_id is None
        assert draft.company_id == company_id and draft.lead_id == lead.id
        assert draft.task_id == task.id and draft.recipient_email == "office@studio.example"
        assert lead is not None and lead.company_id == company_id and lead.contact_id is None
        assert task is not None and task.lead_id == lead.id

    second = _run(project_id, lambda: generator)

    assert second.completed_count == 0
    assert second.drafts_created == 0
    assert second.drafts_reused == second.attempt_budget
    assert _counts() == first_counts
    assert len(generator.calls) == 1


def test_person_scoped_completion_is_verified_from_fresh_session(monkeypatch) -> None:
    project_id, company_id = _seed_company(company_email="office@studio.example")
    person = _seed_person(company_id)
    _install_boundaries(monkeypatch, company_id=company_id, person=person)

    result = _run(project_id, FakeEmailDraftGenerator)

    assert result.completed_count == 1
    assert result.person_scoped_count == 1
    assert (result.contacts_reused, result.leads_reused, result.tasks_reused) == (1, 1, 1)
    assert _counts() == (1, 1, 1, 1, 0, 0)
    with SessionLocal() as session:
        draft = session.get(EmailDraft, result.completed_draft_ids[0])
        contact = session.get(Contact, person[0])
        lead = session.get(Lead, person[1])
        task = session.get(Task, person[2])
        assert draft is not None and contact is not None and lead is not None and task is not None
        assert draft.contact_id == contact.id and draft.recipient_email == contact.email
        assert draft.company_id == company_id and draft.lead_id == lead.id
        assert draft.task_id == task.id and lead.contact_id == contact.id


def test_no_email_persists_no_draft_or_delivery_state(monkeypatch) -> None:
    project_id, company_id = _seed_company(company_email=None)
    _install_boundaries(monkeypatch, company_id=company_id)

    result = _run(project_id, FakeEmailDraftGenerator)

    assert result.completed_count == 0
    assert result.no_email_count == result.attempt_budget
    assert _counts() == (0, 0, 0, 0, 0, 0)


class _FailingGenerator:
    def generate(self, request):
        raise EmailDraftProviderUnavailableError("offline")

    def close(self) -> None:
        return None


def test_draft_failure_keeps_upstream_state_without_partial_draft(monkeypatch) -> None:
    project_id, company_id = _seed_company()
    _install_boundaries(monkeypatch, company_id=company_id)

    result = _run(project_id, _FailingGenerator)

    assert result.completed_count == 0
    assert result.draft_failure_count == result.attempt_budget
    assert _counts() == (0, 1, 1, 0, 0, 0)
    with SessionLocal() as session:
        company = session.get(Company, company_id)
        lead = session.scalar(select(Lead).where(Lead.company_id == company_id))
        task = session.scalar(select(Task).where(Task.lead_id == lead.id))
        assert company is not None and lead is not None and task is not None


def test_preseeded_company_lead_task_recovers_draft_once_across_fresh_runs(monkeypatch) -> None:
    project_id, company_id = _seed_company()
    lead_id, task_id = _seed_company_outreach(company_id)
    assert _counts() == (0, 1, 1, 0, 0, 0)
    _install_boundaries(monkeypatch, company_id=company_id)
    generator = FakeEmailDraftGenerator()

    recovered = _run(project_id, lambda: generator)

    assert recovered.completed_count == 1
    assert (recovered.companies_created, recovered.contacts_created) == (0, 0)
    assert (recovered.leads_created, recovered.tasks_created) == (0, 0)
    assert (recovered.leads_reused, recovered.tasks_reused) == (1, 1)
    assert (recovered.drafts_created, recovered.drafts_reused) == (1, 0)
    assert recovered.completed_company_ids == (company_id,)
    assert recovered.completed_lead_ids == (lead_id,)
    assert recovered.completed_task_ids == (task_id,)
    recovered_draft_id = recovered.completed_draft_ids[0]
    assert _counts() == (0, 1, 1, 1, 0, 0)
    with SessionLocal() as session:
        draft = session.get(EmailDraft, recovered_draft_id)
        assert draft is not None
        assert draft.company_id == company_id
        assert draft.lead_id == lead_id and draft.task_id == task_id
        assert draft.contact_id is None
        assert draft.recipient_email == "office@studio.example"
        assert draft.status == "DRAFT"

    already_complete = _run(project_id, lambda: generator)

    assert already_complete.completed_count == 0
    assert already_complete.drafts_created == 0
    assert already_complete.drafts_reused == already_complete.attempt_budget
    assert _counts() == (0, 1, 1, 1, 0, 0)
    assert len(generator.calls) == 1


class _FailSecondDraftGenerator:
    def __init__(self) -> None:
        self.delegate = FakeEmailDraftGenerator()
        self.call_count = 0

    def generate(self, request):
        self.call_count += 1
        if self.call_count == 2:
            raise EmailDraftProviderUnavailableError("offline")
        return self.delegate.generate(request)

    def close(self) -> None:
        return None


def test_persisted_first_target_survives_second_target_draft_failure(monkeypatch) -> None:
    project_id, company_a_id = _seed_company()
    company_b_id = _seed_additional_company(project_id)
    candidates = iter((company_a_id, company_b_id))

    def company_plan(*args, **kwargs):
        selected = next(candidates, None)
        return SimpleNamespace(
            discovery_run_id=100 + (selected or 0),
            staged_candidate_count=int(selected is not None),
            selected_candidate_id=selected,
            serpapi_call_count=0,
            openai_call_count=0,
        )

    def company_apply(data, **kwargs):
        return SimpleNamespace(
            company_id=data.candidate_id,
            company_created=False,
            company_reused=True,
        )

    no_contact = SimpleNamespace(
        selected_candidate_id=None,
        selected_contact_email=None,
        handoff_token=None,
        provider_call_count=0,
        decision=SimpleNamespace(value="NO_SELECTION"),
    )
    monkeypatch.setattr("app.modules.agent.execution.execute_company_plan", company_plan)
    monkeypatch.setattr("app.modules.agent.execution.execute_company_apply", company_apply)
    monkeypatch.setattr(
        "app.modules.agent.execution.execute_contact_plan",
        lambda *args, **kwargs: no_contact,
    )
    generator = _FailSecondDraftGenerator()

    result = _run(project_id, lambda: generator, limit=2)

    assert result.completed_count == 1
    assert result.draft_failure_count == 1
    assert result.completed_company_ids == (company_a_id,)
    assert company_b_id not in result.completed_company_ids
    assert _counts() == (0, 2, 2, 1, 0, 0)
    with SessionLocal() as session:
        company_a = session.get(Company, company_a_id)
        company_b = session.get(Company, company_b_id)
        lead_a = session.scalar(select(Lead).where(Lead.company_id == company_a_id))
        lead_b = session.scalar(select(Lead).where(Lead.company_id == company_b_id))
        task_a = session.scalar(select(Task).where(Task.lead_id == lead_a.id))
        task_b = session.scalar(select(Task).where(Task.lead_id == lead_b.id))
        draft_a = session.scalar(select(EmailDraft).where(EmailDraft.company_id == company_a_id))
        draft_b = session.scalar(select(EmailDraft).where(EmailDraft.company_id == company_b_id))
        assert company_a is not None and company_b is not None
        assert lead_a is not None and lead_b is not None
        assert task_a is not None and task_b is not None
        assert draft_a is not None and draft_a.status == "DRAFT"
        assert draft_a.lead_id == lead_a.id and draft_a.task_id == task_a.id
        assert draft_a.recipient_email == "office@studio.example"
        assert draft_b is None

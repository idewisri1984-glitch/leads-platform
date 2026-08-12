from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.modules.lead.models import Lead
from app.modules.task.models import Task, TaskLifecycleStatus


@dataclass(frozen=True, slots=True)
class CRMCompanyRecord:
    id: int
    project_id: int
    name: str


@dataclass(frozen=True, slots=True)
class CRMContactRecord:
    id: int
    company_id: int
    first_name: str | None
    last_name: str | None
    job_title: str | None
    email: str | None


@dataclass(frozen=True, slots=True)
class CRMLeadRecord:
    id: int
    company_id: int
    contact_id: int | None
    status: str


@dataclass(frozen=True, slots=True)
class CRMTaskRecord:
    id: int
    lead_id: int
    title: str
    status: str
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class CRMDraftRecord:
    id: int
    lead_id: int
    task_id: int
    status: str
    generated_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CRMManualSendRecord:
    email_draft_id: int
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class CRMDeliveryAttemptRecord:
    id: int
    email_draft_id: int
    outcome: str
    created_at: datetime
    accepted_at: datetime | None


@dataclass(frozen=True, slots=True)
class CRMOverviewSnapshot:
    companies: tuple[CRMCompanyRecord, ...]
    contacts: tuple[CRMContactRecord, ...]
    leads: tuple[CRMLeadRecord, ...]
    tasks: tuple[CRMTaskRecord, ...]
    drafts: tuple[CRMDraftRecord, ...]
    manual_sends: tuple[CRMManualSendRecord, ...]
    delivery_attempts: tuple[CRMDeliveryAttemptRecord, ...]


@dataclass(frozen=True, slots=True)
class CRMOverviewRow:
    company_id: int
    company: str
    contact_id: int | None
    contact: str | None
    role: str | None
    email: str | None
    lead_id: int | None
    lead_status: str | None
    task_id: int | None
    task: str | None
    task_status: str | None
    draft_id: int | None
    draft_task_id: int | None
    draft_status: str | None
    outreach_status: str
    last_sent_at: datetime | None

    def as_dict(self) -> dict[str, datetime | int | str | None]:
        return asdict(self)


class CRMOverviewRepository:
    """Load a bounded CRM snapshot with no writes and no per-row queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, project_id: int | None, company_id: int | None) -> CRMOverviewSnapshot:
        company_query = select(Company.id, Company.project_id, Company.name)
        if project_id is not None:
            company_query = company_query.where(Company.project_id == project_id)
        if company_id is not None:
            company_query = company_query.where(Company.id == company_id)
        company_values = self.session.execute(
            company_query.order_by(Company.name, Company.id)
        ).all()
        companies = tuple(CRMCompanyRecord(*value) for value in company_values)
        company_ids = [company.id for company in companies]
        if not company_ids:
            return CRMOverviewSnapshot(companies, (), (), (), (), (), ())

        contact_values = self.session.execute(
            select(
                Contact.id,
                Contact.company_id,
                Contact.first_name,
                Contact.last_name,
                Contact.job_title,
                Contact.email,
            ).where(Contact.company_id.in_(company_ids))
        ).all()
        contacts = tuple(CRMContactRecord(*value) for value in contact_values)
        lead_values = self.session.execute(
            select(Lead.id, Lead.company_id, Lead.contact_id, Lead.status).where(
                Lead.company_id.in_(company_ids)
            )
        ).all()
        leads = tuple(CRMLeadRecord(*value) for value in lead_values)
        lead_ids = [lead.id for lead in leads]
        if not lead_ids:
            return CRMOverviewSnapshot(companies, contacts, (), (), (), (), ())

        task_values = self.session.execute(
            select(Task.id, Task.lead_id, Task.title, Task.status, Task.due_at).where(
                Task.lead_id.in_(lead_ids)
            )
        ).all()
        tasks = tuple(CRMTaskRecord(*value) for value in task_values)
        draft_values = self.session.execute(
            select(
                EmailDraft.id,
                EmailDraft.lead_id,
                EmailDraft.task_id,
                EmailDraft.status,
                EmailDraft.generated_at,
                EmailDraft.created_at,
            ).where(EmailDraft.lead_id.in_(lead_ids))
        ).all()
        drafts = tuple(CRMDraftRecord(*value) for value in draft_values)
        draft_ids = [draft.id for draft in drafts]
        if not draft_ids:
            return CRMOverviewSnapshot(companies, contacts, leads, tasks, (), (), ())

        manual_values = self.session.execute(
            select(ManualEmailSendRecord.email_draft_id, ManualEmailSendRecord.sent_at).where(
                ManualEmailSendRecord.email_draft_id.in_(draft_ids)
            )
        ).all()
        attempt_values = self.session.execute(
            select(
                EmailDeliveryAttempt.id,
                EmailDeliveryAttempt.email_draft_id,
                EmailDeliveryAttempt.outcome,
                EmailDeliveryAttempt.created_at,
                EmailDeliveryAttempt.accepted_at,
            ).where(EmailDeliveryAttempt.email_draft_id.in_(draft_ids))
        ).all()
        return CRMOverviewSnapshot(
            companies,
            contacts,
            leads,
            tasks,
            drafts,
            tuple(CRMManualSendRecord(*value) for value in manual_values),
            tuple(CRMDeliveryAttemptRecord(*value) for value in attempt_values),
        )


def derive_outreach_status(
    draft: CRMDraftRecord | None,
    manual_send: CRMManualSendRecord | None,
    attempt: CRMDeliveryAttemptRecord | None,
) -> tuple[str, datetime | None]:
    if manual_send is not None:
        return "MANUALLY_SENT", manual_send.sent_at
    if attempt is not None:
        status = f"AUTOMATIC_{attempt.outcome}"
        sent_at = (
            attempt.accepted_at if attempt.outcome == EmailDeliveryOutcome.ACCEPTED.value else None
        )
        return status, sent_at
    if draft is None:
        return "NO_DRAFT", None
    if draft.status == EmailDraftStatus.APPROVED.value:
        return "APPROVED", None
    if draft.status == EmailDraftStatus.REJECTED.value:
        return "REJECTED", None
    return "DRAFT", None


class CRMOverviewService:
    """Compose stable CRM rows from an already loaded authoritative snapshot."""

    def build(self, snapshot: CRMOverviewSnapshot) -> tuple[CRMOverviewRow, ...]:
        contacts_by_company: dict[int, list[CRMContactRecord]] = {}
        leads_by_company: dict[int, list[CRMLeadRecord]] = {}
        tasks_by_lead: dict[int, list[CRMTaskRecord]] = {}
        drafts_by_lead: dict[int, list[CRMDraftRecord]] = {}
        manual_by_draft = {record.email_draft_id: record for record in snapshot.manual_sends}
        attempts_by_draft: dict[int, list[CRMDeliveryAttemptRecord]] = {}
        for contact in snapshot.contacts:
            contacts_by_company.setdefault(contact.company_id, []).append(contact)
        for lead in snapshot.leads:
            leads_by_company.setdefault(lead.company_id, []).append(lead)
        for task in snapshot.tasks:
            tasks_by_lead.setdefault(task.lead_id, []).append(task)
        for draft in snapshot.drafts:
            drafts_by_lead.setdefault(draft.lead_id, []).append(draft)
        for attempt in snapshot.delivery_attempts:
            attempts_by_draft.setdefault(attempt.email_draft_id, []).append(attempt)

        rows: list[CRMOverviewRow] = []
        for company in snapshot.companies:
            company_contacts = sorted(
                contacts_by_company.get(company.id, []), key=self._contact_sort_key
            )
            company_leads = sorted(leads_by_company.get(company.id, []), key=lambda item: item.id)
            leads_by_contact: dict[int, list[CRMLeadRecord]] = {}
            unassigned_leads: list[CRMLeadRecord] = []
            for lead in company_leads:
                if lead.contact_id is None:
                    unassigned_leads.append(lead)
                else:
                    leads_by_contact.setdefault(lead.contact_id, []).append(lead)
            for contact in company_contacts:
                contact_leads = leads_by_contact.get(contact.id, [])
                if contact_leads:
                    for lead in contact_leads:
                        rows.append(
                            self._row(
                                company,
                                contact,
                                lead,
                                tasks_by_lead,
                                drafts_by_lead,
                                manual_by_draft,
                                attempts_by_draft,
                            )
                        )
                else:
                    rows.append(self._row(company, contact, None, {}, {}, {}, {}))
            for lead in unassigned_leads:
                rows.append(
                    self._row(
                        company,
                        None,
                        lead,
                        tasks_by_lead,
                        drafts_by_lead,
                        manual_by_draft,
                        attempts_by_draft,
                    )
                )
            if not company_contacts and not company_leads:
                rows.append(self._row(company, None, None, {}, {}, {}, {}))
        return tuple(rows)

    @staticmethod
    def _contact_sort_key(contact: CRMContactRecord) -> tuple[str, str, int]:
        return (
            (contact.last_name or "").casefold(),
            (contact.first_name or "").casefold(),
            contact.id,
        )

    @staticmethod
    def _select_task(tasks: list[CRMTaskRecord]) -> CRMTaskRecord | None:
        active = [
            task
            for task in tasks
            if task.status
            in {TaskLifecycleStatus.TODO.value, TaskLifecycleStatus.IN_PROGRESS.value}
        ]
        if active:

            def active_key(task: CRMTaskRecord) -> tuple[int, int, str, int]:
                status_rank = 0 if task.status == TaskLifecycleStatus.IN_PROGRESS.value else 1
                return (
                    status_rank,
                    task.due_at is None,
                    task.due_at.isoformat() if task.due_at else "",
                    task.id,
                )

            return min(active, key=active_key)
        done = [task for task in tasks if task.status == TaskLifecycleStatus.DONE.value]
        if done:
            return max(done, key=lambda task: task.id)
        cancelled = [task for task in tasks if task.status == TaskLifecycleStatus.CANCELLED.value]
        return max(cancelled, key=lambda task: task.id) if cancelled else None

    @staticmethod
    def _select_draft(
        drafts: list[CRMDraftRecord], selected_task: CRMTaskRecord | None
    ) -> CRMDraftRecord | None:
        relevant = (
            [draft for draft in drafts if draft.task_id == selected_task.id]
            if selected_task is not None
            else []
        )
        candidates = relevant or drafts
        return max(
            candidates,
            key=lambda draft: (draft.generated_at, draft.created_at, draft.id),
            default=None,
        )

    def _row(
        self,
        company: CRMCompanyRecord,
        contact: CRMContactRecord | None,
        lead: CRMLeadRecord | None,
        tasks_by_lead: dict[int, list[CRMTaskRecord]],
        drafts_by_lead: dict[int, list[CRMDraftRecord]],
        manual_by_draft: dict[int, CRMManualSendRecord],
        attempts_by_draft: dict[int, list[CRMDeliveryAttemptRecord]],
    ) -> CRMOverviewRow:
        task = self._select_task(tasks_by_lead.get(lead.id, [])) if lead else None
        draft = self._select_draft(drafts_by_lead.get(lead.id, []), task) if lead else None
        manual = manual_by_draft.get(draft.id) if draft else None
        attempt = (
            max(attempts_by_draft.get(draft.id, []), key=lambda item: item.id, default=None)
            if draft
            else None
        )
        outreach_status, last_sent_at = derive_outreach_status(draft, manual, attempt)
        contact_name = None
        if contact is not None:
            contact_name = (
                " ".join(part for part in (contact.first_name, contact.last_name) if part) or None
            )
        return CRMOverviewRow(
            company_id=company.id,
            company=company.name,
            contact_id=contact.id if contact else None,
            contact=contact_name,
            role=contact.job_title if contact else None,
            email=contact.email if contact else None,
            lead_id=lead.id if lead else None,
            lead_status=lead.status if lead else None,
            task_id=task.id if task else None,
            task=task.title if task else None,
            task_status=task.status if task else None,
            draft_id=draft.id if draft else None,
            draft_task_id=draft.task_id if draft else None,
            draft_status=draft.status if draft else None,
            outreach_status=outreach_status,
            last_sent_at=last_sent_at,
        )

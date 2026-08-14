from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.contact_discovery.normalization import normalize_discovered_email
from app.modules.lead.models import Lead
from app.modules.task.models import Task, TaskLifecycleStatus

from .context import EMAIL_DRAFT_PROMPT_VERSION
from .models import EmailDraft
from .provider_interfaces import EmailDraftGenerator
from .repository import EmailDraftRepository
from .schemas import (
    EmailDraftGenerationInput,
    EmailDraftGenerationResult,
    EmailDraftProviderRequest,
    EmailLanguage,
    EmailTone,
)
from .service import EmailDraftService


class MissingDraftResultStatus(StrEnum):
    CREATED = "CREATED"
    WOULD_CREATE = "WOULD_CREATE"
    SKIPPED_EXISTING = "SKIPPED_EXISTING"
    SKIPPED_NO_EMAIL = "SKIPPED_NO_EMAIL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MissingDraftBatchOptions:
    project_id: int
    limit: int
    sender_name: str
    sender_company: str
    purpose: str
    value_proposition: str | None = None
    language: EmailLanguage = EmailLanguage.EN
    tone: EmailTone = EmailTone.PROFESSIONAL
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class MissingDraftBatchItem:
    company_id: int
    company_name: str
    recipient_type: str
    recipient_email: str | None
    decision_maker: str | None
    contact_id: int | None
    result: MissingDraftResultStatus
    draft_id: int | None = None
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class MissingDraftBatchResult:
    candidate_count: int
    selected_limit: int
    ai_call_count: int
    items: tuple[MissingDraftBatchItem, ...]


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class _LazyCountingGenerator:
    def __init__(self, factory: Callable[[], EmailDraftGenerator]) -> None:
        self._factory = factory
        self._generator: EmailDraftGenerator | None = None
        self.call_count = 0

    def generate(self, request: EmailDraftProviderRequest) -> EmailDraftGenerationResult:
        if self._generator is None:
            self._generator = self._factory()
        self.call_count += 1
        return self._generator.generate(request)

    def close(self) -> None:
        if self._generator is None:
            return
        close = getattr(self._generator, "close", None)
        if callable(close):
            close()


def _usable_email(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        return normalize_discovered_email(value)
    except (TypeError, ValueError):
        return None


class MissingEmailDraftBatchService:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        generator_factory: Callable[[], EmailDraftGenerator],
    ) -> None:
        self._session_factory = session_factory
        self._generator_factory = generator_factory

    def run(self, options: MissingDraftBatchOptions) -> MissingDraftBatchResult:
        if options.project_id <= 0 or options.limit <= 0:
            raise ValueError("Missing draft batch options are invalid.")
        candidate_ids = self._candidate_task_ids(options.project_id, options.limit)
        generator = _LazyCountingGenerator(self._generator_factory)
        items: list[MissingDraftBatchItem] = []
        try:
            for task_id in candidate_ids:
                session = self._session_factory()
                try:
                    item = self._process(
                        session=session,
                        task_id=task_id,
                        options=options,
                        generator=generator,
                    )
                    if not options.dry_run and item.result is MissingDraftResultStatus.CREATED:
                        session.commit()
                    else:
                        session.rollback()
                    items.append(item)
                except Exception:
                    session.rollback()
                    company = session.scalar(
                        select(Company)
                        .join(Lead, Lead.company_id == Company.id)
                        .join(Task, Task.lead_id == Lead.id)
                        .where(Task.id == task_id)
                    )
                    items.append(
                        MissingDraftBatchItem(
                            company_id=company.id if company is not None else 0,
                            company_name=company.name if company is not None else "Unknown",
                            recipient_type="UNKNOWN",
                            recipient_email=None,
                            decision_maker=None,
                            contact_id=None,
                            result=MissingDraftResultStatus.FAILED,
                        )
                    )
                finally:
                    session.close()
        finally:
            generator.close()
        return MissingDraftBatchResult(
            candidate_count=len(candidate_ids),
            selected_limit=options.limit,
            ai_call_count=generator.call_count,
            items=tuple(items),
        )

    def _candidate_task_ids(self, project_id: int, limit: int) -> list[int]:
        session = self._session_factory()
        try:
            rows = session.execute(
                select(Company.id, Task.id)
                .join(Lead, Lead.company_id == Company.id)
                .join(Task, Task.lead_id == Lead.id)
                .where(
                    Company.project_id == project_id,
                    Task.status.in_(
                        (TaskLifecycleStatus.TODO.value, TaskLifecycleStatus.IN_PROGRESS.value)
                    ),
                )
                .order_by(Company.id, Task.id.desc())
            )
            selected: list[int] = []
            seen: set[int] = set()
            for company_id, task_id in rows:
                if company_id in seen:
                    continue
                seen.add(company_id)
                selected.append(task_id)
                if len(selected) == limit:
                    break
            return selected
        finally:
            session.close()

    def _process(
        self,
        *,
        session: Session,
        task_id: int,
        options: MissingDraftBatchOptions,
        generator: EmailDraftGenerator,
    ) -> MissingDraftBatchItem:
        task = session.get(Task, task_id)
        if task is None:
            raise ValueError("Missing draft task was not found.")
        lead = session.get(Lead, task.lead_id)
        if lead is None:
            raise ValueError("Missing draft lead was not found.")
        company = session.get(Company, lead.company_id)
        if company is None or company.project_id != options.project_id:
            raise ValueError("Missing draft company was not found.")
        existing = session.scalar(
            select(EmailDraft).where(EmailDraft.task_id == task.id).order_by(EmailDraft.id)
        )
        if existing is not None:
            return MissingDraftBatchItem(
                company.id,
                company.name,
                "DECISION_MAKER" if existing.contact_id is not None else "COMPANY",
                existing.recipient_email,
                existing.recipient_name if existing.contact_id is not None else None,
                existing.contact_id,
                MissingDraftResultStatus.SKIPPED_EXISTING,
                existing.id,
                existing.subject,
            )
        contact = session.get(Contact, lead.contact_id) if lead.contact_id is not None else None
        contact_email = _usable_email(contact.email) if contact is not None else None
        enrichment = session.scalar(
            select(CompanyEnrichment).where(CompanyEnrichment.company_id == company.id)
        )
        company_email = _usable_email(enrichment.email) if enrichment is not None else None
        if contact_email is not None and contact is not None:
            contact_id = contact.id
            recipient_type = "DECISION_MAKER"
            recipient_email = contact_email
            decision_maker = " ".join(
                value for value in (contact.first_name, contact.last_name) if value
            )
        elif company_email is not None:
            contact_id = None
            recipient_type = "COMPANY"
            recipient_email = company_email
            decision_maker = None
        else:
            return MissingDraftBatchItem(
                company.id,
                company.name,
                "NO_EMAIL",
                None,
                None,
                None,
                MissingDraftResultStatus.SKIPPED_NO_EMAIL,
            )
        if options.dry_run:
            return MissingDraftBatchItem(
                company.id,
                company.name,
                recipient_type,
                recipient_email,
                decision_maker,
                contact_id,
                MissingDraftResultStatus.WOULD_CREATE,
            )
        result = EmailDraftService(
            session=session,
            repository=EmailDraftRepository(session),
            generator=generator,
        ).generate(
            EmailDraftGenerationInput(
                project_id=options.project_id,
                company_id=company.id,
                contact_id=contact_id,
                lead_id=lead.id,
                task_id=task.id,
                sender_name=options.sender_name,
                sender_company=options.sender_company,
                language=options.language,
                tone=options.tone,
                purpose=options.purpose,
                value_proposition=options.value_proposition,
                prompt_version=EMAIL_DRAFT_PROMPT_VERSION,
            )
        )
        return MissingDraftBatchItem(
            company.id,
            company.name,
            recipient_type,
            recipient_email,
            decision_maker,
            contact_id,
            MissingDraftResultStatus.CREATED,
            result.id,
            result.subject,
        )

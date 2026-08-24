from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, overload
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.company_import.normalization import normalize_website_hostname
from app.modules.contact.models import Contact
from app.modules.contact_discovery.normalization import normalize_discovered_email
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task, TaskLifecycleStatus

from .context import (
    EmailDraftSourceRecords,
    build_content_hash,
    build_context_fingerprint,
    build_email_personalization_context,
)
from .models import EmailDraft, EmailDraftStatus
from .recipient_rebinding_schemas import (
    PersonRecipientRebindingInput,
    PersonRecipientRebindingResult,
)
from .repository import EmailDraftRepository
from .schemas import EmailDraftGenerationInput, EmailDraftRead, EmailLanguage, EmailTone

_INVALID = "Person recipient rebinding data is invalid."
_CONFIRMATION = "Person recipient rebinding requires --confirm."
_NOT_FOUND = "Person recipient rebinding target was not found."
_NOT_ELIGIBLE = "Person recipient rebinding target is not eligible."
_CONFLICT = "Person recipient rebinding conflicts with persisted data."
_STALE = "Email draft content changed before recipient rebinding."
_ALREADY_SENT = "Email draft delivery is already claimed."
_PERSISTENCE = "Person recipient rebinding could not be persisted."
_INTERNAL = "Person recipient rebinding failed."
_CONTACT_SOURCE = "OFFICIAL_WEBSITE"


class PersonRecipientRebindingError(ValueError):
    pass


class PersonRecipientRebindingInvalidDataError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingConfirmationRequiredError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingNotFoundError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingNotEligibleError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingConflictError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingStaleContextError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingAlreadySentError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingPersistenceError(PersonRecipientRebindingError):
    pass


class PersonRecipientRebindingInternalError(PersonRecipientRebindingError):
    pass


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class PersonRecipientRebindingService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def rebind(self, data: PersonRecipientRebindingInput) -> PersonRecipientRebindingResult:
        validated = self._validated_input(data)
        try:
            session = self._session_factory()
        except Exception:
            raise PersonRecipientRebindingPersistenceError(_PERSISTENCE) from None
        committed = False
        try:
            result = self._apply(session, validated)
            try:
                session.commit()
                committed = True
            except IntegrityError:
                raise PersonRecipientRebindingConflictError(_CONFLICT) from None
            except Exception:
                raise PersonRecipientRebindingPersistenceError(_PERSISTENCE) from None
            return result
        except PersonRecipientRebindingError:
            if not committed:
                self._cleanup(session.rollback)
            raise
        except IntegrityError:
            if not committed:
                self._cleanup(session.rollback)
            raise PersonRecipientRebindingConflictError(_CONFLICT) from None
        except SQLAlchemyError:
            if not committed:
                self._cleanup(session.rollback)
            raise PersonRecipientRebindingPersistenceError(_PERSISTENCE) from None
        except BaseException as error:
            if not committed:
                self._cleanup(session.rollback)
            if not isinstance(error, Exception):
                raise
            raise PersonRecipientRebindingInternalError(_INTERNAL) from None
        finally:
            self._cleanup(session.close)

    def _apply(
        self, session: Session, data: PersonRecipientRebindingInput
    ) -> PersonRecipientRebindingResult:
        try:
            CompanyRepository(session).acquire_promotion_scope(data.project_id)
        except ValueError:
            raise PersonRecipientRebindingNotFoundError(_NOT_FOUND) from None

        project = self._locked(session, Project, data.project_id)
        company = self._locked(session, Company, data.company_id)
        enrichment = session.scalar(
            select(CompanyEnrichment)
            .where(CompanyEnrichment.company_id == data.company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        lead = self._locked(session, Lead, data.lead_id)
        task = self._locked(session, Task, data.task_id)
        draft = self._locked(session, EmailDraft, data.email_draft_id)
        if any(record is None for record in (project, company, enrichment, lead, task, draft)):
            raise PersonRecipientRebindingNotFoundError(_NOT_FOUND)
        assert project is not None
        assert company is not None
        assert enrichment is not None
        assert lead is not None
        assert task is not None
        assert draft is not None

        self._validate_scope(data, project, company, lead, task, draft)
        self._validate_urls(data, company)
        self._validate_eligibility(session, data, enrichment, lead, task, draft)
        self._validate_location(company.country, data.country)
        self._validate_location(company.city, data.city)

        contacts = list(
            session.scalars(
                select(Contact)
                .where(
                    Contact.company_id == company.id,
                    Contact.email.is_not(None),
                    func.lower(func.trim(Contact.email)) == data.recipient_email,
                )
                .order_by(Contact.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if len(contacts) > 1:
            raise PersonRecipientRebindingConflictError(_CONFLICT)
        contact = contacts[0] if contacts else None
        if contact is not None:
            self._validate_contact(contact, data)
        self._validate_binding_state(lead, draft, contact)

        country_before = company.country
        city_before = company.city
        lead_contact_before = lead.contact_id
        draft_contact_before = draft.contact_id
        recipient_name_before = draft.recipient_name
        recipient_role_before = draft.recipient_role
        context_before = draft.context_fingerprint
        request_before = draft.request_fingerprint
        content_before = draft.content_hash

        contact_created = contact is None
        if contact is None:
            contact = Contact(
                company_id=company.id,
                first_name=data.first_name,
                last_name=data.last_name,
                job_title=data.job_title,
                email=data.recipient_email,
                country=data.country,
                city=data.city,
                source=_CONTACT_SOURCE,
                status="NEW",
                notes=data.person_source_url,
            )
            session.add(contact)
            session.flush()
        else:
            self._fill_contact_evidence(contact, data)

        company.country = data.country
        company.city = data.city
        lead.contact_id = contact.id
        recipient_name = f"{contact.first_name} {contact.last_name}".strip()
        draft.contact_id = contact.id
        draft.recipient_name = recipient_name
        draft.recipient_role = contact.job_title

        generation = self._generation(draft, data, contact.id)
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
        collision = EmailDraftRepository(session).find_by_request_fingerprint(fingerprint)
        if collision is not None and collision.id != draft.id:
            raise PersonRecipientRebindingConflictError(_CONFLICT)
        draft.context_fingerprint = fingerprint
        draft.request_fingerprint = fingerprint
        calculated_hash = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=draft.text_body,
            prompt_version=draft.prompt_version,
        )
        if calculated_hash != content_before or calculated_hash != data.expected_content_hash:
            raise PersonRecipientRebindingStaleContextError(_STALE)

        session.flush()
        self._validate_final_draft(draft, contact)
        changed = any(
            (
                contact_created,
                country_before != company.country,
                city_before != company.city,
                lead_contact_before != lead.contact_id,
                draft_contact_before != draft.contact_id,
                recipient_name_before != draft.recipient_name,
                recipient_role_before != draft.recipient_role,
                context_before != draft.context_fingerprint,
                request_before != draft.request_fingerprint,
            )
        )
        return self._build_result(
            data=data,
            company=company,
            contact=contact,
            lead=lead,
            draft=draft,
            contact_created=contact_created,
            country_before=country_before,
            city_before=city_before,
            lead_contact_before=lead_contact_before,
            draft_contact_before=draft_contact_before,
            recipient_name_before=recipient_name_before,
            recipient_role_before=recipient_role_before,
            context_before=context_before,
            request_before=request_before,
            content_before=content_before,
            changed=changed,
        )

    @staticmethod
    @overload
    def _locked(
        session: Session,
        model: type[Project],
        record_id: int,
    ) -> Project | None: ...

    @staticmethod
    @overload
    def _locked(
        session: Session,
        model: type[Company],
        record_id: int,
    ) -> Company | None: ...

    @staticmethod
    @overload
    def _locked(
        session: Session,
        model: type[Lead],
        record_id: int,
    ) -> Lead | None: ...

    @staticmethod
    @overload
    def _locked(
        session: Session,
        model: type[Task],
        record_id: int,
    ) -> Task | None: ...

    @staticmethod
    @overload
    def _locked(
        session: Session,
        model: type[EmailDraft],
        record_id: int,
    ) -> EmailDraft | None: ...

    @staticmethod
    def _locked(
        session: Session,
        model: type[Project] | type[Company] | type[Lead] | type[Task] | type[EmailDraft],
        record_id: int,
    ) -> Base | None:
        return session.scalar(
            select(model)
            .where(model.id == record_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _validate_scope(
        data: PersonRecipientRebindingInput,
        project: Project,
        company: Company,
        lead: Lead,
        task: Task,
        draft: EmailDraft,
    ) -> None:
        if (
            company.project_id != project.id
            or lead.company_id != company.id
            or task.lead_id != lead.id
            or draft.project_id != project.id
            or draft.company_id != company.id
            or draft.lead_id != lead.id
            or draft.task_id != task.id
            or data.project_id != project.id
            or data.company_id != company.id
            or data.lead_id != lead.id
            or data.task_id != task.id
            or data.email_draft_id != draft.id
        ):
            raise PersonRecipientRebindingConflictError(_CONFLICT)

    @staticmethod
    def _validate_urls(data: PersonRecipientRebindingInput, company: Company) -> None:
        try:
            company_host = normalize_website_hostname(company.website)
            person_host = normalize_website_hostname(data.person_source_url)
            location_host = normalize_website_hostname(data.location_source_url)
        except ValueError:
            raise PersonRecipientRebindingInvalidDataError(_INVALID) from None
        if company_host is None or person_host != company_host or location_host != company_host:
            raise PersonRecipientRebindingInvalidDataError(_INVALID)
        if any(
            urlsplit(url).scheme != "https"
            for url in (data.person_source_url, data.location_source_url)
        ):
            raise PersonRecipientRebindingInvalidDataError(_INVALID)

    @staticmethod
    def _validate_eligibility(
        session: Session,
        data: PersonRecipientRebindingInput,
        enrichment: CompanyEnrichment,
        lead: Lead,
        task: Task,
        draft: EmailDraft,
    ) -> None:
        manual = session.scalar(
            select(ManualEmailSendRecord).where(ManualEmailSendRecord.email_draft_id == draft.id)
        )
        attempt = session.scalar(
            select(EmailDeliveryAttempt).where(EmailDeliveryAttempt.email_draft_id == draft.id)
        )
        if manual is not None or attempt is not None or draft.delivery_mode is not None:
            raise PersonRecipientRebindingAlreadySentError(_ALREADY_SENT)
        if (
            draft.status != EmailDraftStatus.DRAFT.value
            or lead.status != "NEW"
            or task.status
            not in {TaskLifecycleStatus.TODO.value, TaskLifecycleStatus.IN_PROGRESS.value}
            or draft.reviewed_at is not None
            or draft.approved_at is not None
            or draft.rejected_at is not None
        ):
            raise PersonRecipientRebindingNotEligibleError(_NOT_ELIGIBLE)
        try:
            draft_email = normalize_discovered_email(draft.recipient_email)
            enrichment_email = normalize_discovered_email(enrichment.email)
        except (TypeError, ValueError):
            raise PersonRecipientRebindingConflictError(_CONFLICT) from None
        if draft_email != data.recipient_email or enrichment_email != data.recipient_email:
            raise PersonRecipientRebindingConflictError(_CONFLICT)
        calculated = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=draft.text_body,
            prompt_version=draft.prompt_version,
        )
        if draft.content_hash != data.expected_content_hash or calculated != draft.content_hash:
            raise PersonRecipientRebindingStaleContextError(_STALE)

    @staticmethod
    def _validate_location(existing: str | None, requested: str) -> None:
        if existing is not None and existing.casefold() != requested.casefold():
            raise PersonRecipientRebindingConflictError(_CONFLICT)

    @staticmethod
    def _validate_contact(contact: Contact, data: PersonRecipientRebindingInput) -> None:
        try:
            email = normalize_discovered_email(contact.email)
        except (TypeError, ValueError):
            raise PersonRecipientRebindingConflictError(_CONFLICT) from None
        if email != data.recipient_email or contact.company_id != data.company_id:
            raise PersonRecipientRebindingConflictError(_CONFLICT)
        for existing, requested in (
            (contact.first_name, data.first_name),
            (contact.last_name, data.last_name),
            (contact.job_title, data.job_title),
            (contact.country, data.country),
            (contact.city, data.city),
        ):
            if existing is not None and existing.casefold() != requested.casefold():
                raise PersonRecipientRebindingConflictError(_CONFLICT)

    @staticmethod
    def _validate_binding_state(lead: Lead, draft: EmailDraft, contact: Contact | None) -> None:
        if contact is None:
            if lead.contact_id is not None or draft.contact_id is not None:
                raise PersonRecipientRebindingConflictError(_CONFLICT)
            return
        initial = lead.contact_id is None and draft.contact_id is None
        idempotent = lead.contact_id == contact.id and draft.contact_id == contact.id
        if not initial and not idempotent:
            raise PersonRecipientRebindingConflictError(_CONFLICT)

    @staticmethod
    def _fill_contact_evidence(contact: Contact, data: PersonRecipientRebindingInput) -> None:
        if contact.last_name is None:
            contact.last_name = data.last_name
        if contact.job_title is None:
            contact.job_title = data.job_title
        if contact.country is None:
            contact.country = data.country
        if contact.city is None:
            contact.city = data.city
        if contact.source is None:
            contact.source = _CONTACT_SOURCE
        if contact.notes is None:
            contact.notes = data.person_source_url

    @staticmethod
    def _generation(
        draft: EmailDraft, data: PersonRecipientRebindingInput, contact_id: int
    ) -> EmailDraftGenerationInput:
        try:
            return EmailDraftGenerationInput(
                project_id=data.project_id,
                company_id=data.company_id,
                contact_id=contact_id,
                lead_id=data.lead_id,
                task_id=data.task_id,
                sender_name=draft.sender_name,
                sender_company=draft.sender_company,
                language=EmailLanguage(draft.language),
                tone=EmailTone(draft.generation_tone),
                purpose=draft.generation_purpose,
                value_proposition=draft.generation_value_proposition,
                prompt_version=draft.prompt_version,
            )
        except (ValidationError, TypeError, ValueError):
            raise PersonRecipientRebindingConflictError(_CONFLICT) from None

    @staticmethod
    def _validate_final_draft(draft: EmailDraft, contact: Contact) -> None:
        try:
            EmailDraftRead.model_validate(draft)
        except (ValidationError, TypeError, ValueError):
            raise PersonRecipientRebindingPersistenceError(_PERSISTENCE) from None
        if (
            draft.contact_id != contact.id
            or draft.recipient_email != contact.email
            or draft.recipient_role != contact.job_title
        ):
            raise PersonRecipientRebindingConflictError(_CONFLICT)

    @staticmethod
    def _build_result(
        *,
        data: PersonRecipientRebindingInput,
        company: Company,
        contact: Contact,
        lead: Lead,
        draft: EmailDraft,
        contact_created: bool,
        country_before: str | None,
        city_before: str | None,
        lead_contact_before: int | None,
        draft_contact_before: int | None,
        recipient_name_before: str,
        recipient_role_before: str | None,
        context_before: str,
        request_before: str,
        content_before: str,
        changed: bool,
    ) -> PersonRecipientRebindingResult:
        try:
            return PersonRecipientRebindingResult(
                project_id=data.project_id,
                company_id=company.id,
                contact_id=contact.id,
                lead_id=lead.id,
                task_id=data.task_id,
                email_draft_id=draft.id,
                contact_created=contact_created,
                contact_reused=not contact_created,
                country_before=country_before,
                country_after=company.country,
                city_before=city_before,
                city_after=company.city,
                lead_contact_id_before=lead_contact_before,
                lead_contact_id_after=contact.id,
                draft_contact_id_before=draft_contact_before,
                draft_contact_id_after=contact.id,
                recipient_email=draft.recipient_email,
                recipient_name_before=recipient_name_before,
                recipient_name_after=draft.recipient_name,
                recipient_role_before=recipient_role_before,
                recipient_role_after=contact.job_title,
                context_fingerprint_before=context_before,
                context_fingerprint_after=draft.context_fingerprint,
                request_fingerprint_before=request_before,
                request_fingerprint_after=draft.request_fingerprint,
                content_hash_before=content_before,
                content_hash_after=draft.content_hash,
                person_source_url=data.person_source_url,
                location_source_url=data.location_source_url,
                changed=changed,
                network_call_count=0,
                smtp_call_count=0,
            )
        except (ValidationError, TypeError, ValueError):
            raise PersonRecipientRebindingPersistenceError(_PERSISTENCE) from None

    @staticmethod
    def _validated_input(data: PersonRecipientRebindingInput) -> PersonRecipientRebindingInput:
        if type(data) is not PersonRecipientRebindingInput:
            raise PersonRecipientRebindingInvalidDataError(_INVALID)
        try:
            validated = PersonRecipientRebindingInput(**data.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise PersonRecipientRebindingInvalidDataError(_INVALID) from None
        if validated.confirmed is not True:
            raise PersonRecipientRebindingConfirmationRequiredError(_CONFIRMATION)
        return validated

    @staticmethod
    def _cleanup(operation: Callable[[], object]) -> None:
        with suppress(BaseException):
            operation()


__all__ = [
    "PersonRecipientRebindingAlreadySentError",
    "PersonRecipientRebindingConfirmationRequiredError",
    "PersonRecipientRebindingConflictError",
    "PersonRecipientRebindingError",
    "PersonRecipientRebindingInternalError",
    "PersonRecipientRebindingInvalidDataError",
    "PersonRecipientRebindingNotEligibleError",
    "PersonRecipientRebindingNotFoundError",
    "PersonRecipientRebindingPersistenceError",
    "PersonRecipientRebindingService",
    "PersonRecipientRebindingStaleContextError",
]

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task, TaskLifecycleStatus

from .context import (
    EMAIL_DRAFT_PROMPT_VERSION,
    EmailDraftContextError,
    EmailDraftSourceRecords,
    build_content_hash,
    build_context_fingerprint,
    build_email_personalization_context,
    build_provider_request,
)
from .models import EmailDraft, EmailDraftStatus
from .provider_interfaces import EmailDraftGenerator, EmailDraftProviderError
from .repository import EmailDraftRepository
from .schemas import (
    EmailDraftGenerationInput,
    EmailDraftGenerationResult,
    EmailDraftRead,
    EmailDraftReviewInput,
    EmailDraftScopeInput,
    EmailLanguage,
    EmailPersonalizationContext,
    EmailTone,
)

_INVALID = "Email draft data is invalid."
_NOT_FOUND = "Email draft target was not found."
_SCOPE = "Email draft scope is invalid."
_EMAIL = "Email draft recipient email is unusable."
_NOT_ELIGIBLE = "Email draft target is not eligible."
_PROVIDER = "Email draft provider is unavailable."
_MALFORMED = "Email draft provider result is invalid."
_PERSISTENCE = "Email draft could not be persisted."
_CONFLICT = "Email draft conflicts with existing state."
_REVIEWED = "Email draft was already reviewed."
_INTEGRITY = "Email draft content integrity check failed."
_STALE = "Email draft context is stale."
_CONFIRMATION = "Email draft review requires --yes."
_INTERNAL = "Email draft operation failed."


class EmailDraftError(ValueError):
    pass


class EmailDraftInvalidDataError(EmailDraftError):
    pass


class EmailDraftNotFoundError(EmailDraftError):
    pass


class EmailDraftScopeError(EmailDraftError):
    pass


class EmailDraftMissingEmailError(EmailDraftError):
    pass


class EmailDraftNotEligibleError(EmailDraftError):
    pass


class EmailDraftGenerationError(EmailDraftError):
    pass


class EmailDraftMalformedResultError(EmailDraftError):
    pass


class EmailDraftPersistenceError(EmailDraftError):
    pass


class EmailDraftConflictError(EmailDraftError):
    pass


class EmailDraftAlreadyReviewedError(EmailDraftError):
    pass


class EmailDraftIntegrityError(EmailDraftError):
    pass


class EmailDraftStaleContextError(EmailDraftError):
    pass


class EmailDraftConfirmationRequiredError(EmailDraftError):
    pass


class EmailDraftInternalError(EmailDraftError):
    pass


class EmailDraftService:
    def __init__(
        self,
        *,
        session: Session,
        repository: EmailDraftRepository,
        generator: EmailDraftGenerator | None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.generator = generator

    def generate(self, data: EmailDraftGenerationInput) -> EmailDraftRead:
        try:
            return self._generate(self._validated_generation(data))
        except EmailDraftError:
            raise
        except IntegrityError:
            raise EmailDraftConflictError(_CONFLICT) from None
        except Exception:
            raise EmailDraftInternalError(_INTERNAL) from None

    def show(self, data: EmailDraftScopeInput) -> EmailDraftRead:
        if type(data) is not EmailDraftScopeInput:
            raise EmailDraftInvalidDataError(_INVALID)
        draft = self._get_draft(data, for_update=False)
        return self._read(draft)

    def approve(self, review: EmailDraftReviewInput) -> EmailDraftRead:
        return self._review(review, EmailDraftStatus.APPROVED)

    def reject(self, review: EmailDraftReviewInput) -> EmailDraftRead:
        return self._review(review, EmailDraftStatus.REJECTED)

    def _generate(self, data: EmailDraftGenerationInput) -> EmailDraftRead:
        initial_records = self._load_records(data)
        context = self._context(initial_records)
        fingerprint = build_context_fingerprint(context, data)
        existing = self.repository.find_by_request_fingerprint(fingerprint)
        if existing is not None:
            if self.repository.is_reusable(existing):
                return self._read(existing)
            raise EmailDraftConflictError(_CONFLICT)
        if self.generator is None:
            raise EmailDraftGenerationError(_PROVIDER)
        request = build_provider_request(context, data)
        try:
            raw_result = self.generator.generate(request)
        except EmailDraftProviderError:
            raise EmailDraftGenerationError(_PROVIDER) from None
        result = self._validated_result(raw_result, data)
        fresh_context = self._context(self._load_records(data, populate_existing=True))
        fresh_fingerprint = build_context_fingerprint(fresh_context, data)
        if fresh_fingerprint != fingerprint:
            raise EmailDraftStaleContextError(_STALE)
        content_hash = build_content_hash(
            recipient_email=context.recipient_email,
            subject=result.subject,
            text_body=result.text_body,
            prompt_version=result.prompt_version,
        )
        draft = EmailDraft(
            project_id=data.project_id,
            company_id=data.company_id,
            contact_id=data.contact_id,
            lead_id=data.lead_id,
            task_id=data.task_id,
            recipient_email=context.recipient_email,
            recipient_name=context.recipient_name,
            recipient_role=context.recipient_role,
            sender_name=data.sender_name,
            sender_company=data.sender_company,
            generation_tone=data.tone.value,
            generation_purpose=data.purpose,
            generation_value_proposition=data.value_proposition,
            subject=result.subject,
            text_body=result.text_body,
            language=result.language.value,
            prompt_version=result.prompt_version,
            provider=result.provider,
            model=result.model,
            context_fingerprint=fingerprint,
            request_fingerprint=fingerprint,
            content_hash=content_hash,
            status=EmailDraftStatus.DRAFT.value,
        )
        try:
            self.repository.add(draft)
        except IntegrityError:
            raise EmailDraftConflictError(_CONFLICT) from None
        return self._read(draft)

    def _review(self, review: EmailDraftReviewInput, target: EmailDraftStatus) -> EmailDraftRead:
        data = self._validated_review(review)
        draft = self._get_draft(data, for_update=True)
        if draft.status != EmailDraftStatus.DRAFT.value:
            raise EmailDraftAlreadyReviewedError(_REVIEWED)
        expected_hash = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=draft.text_body,
            prompt_version=draft.prompt_version,
        )
        if expected_hash != draft.content_hash:
            raise EmailDraftIntegrityError(_INTEGRITY)
        generation = EmailDraftGenerationInput(
            project_id=draft.project_id,
            company_id=draft.company_id,
            contact_id=draft.contact_id,
            lead_id=draft.lead_id,
            task_id=draft.task_id,
            sender_name=draft.sender_name,
            sender_company=draft.sender_company,
            language=self._language_from_draft(draft),
            tone=self._tone_from_fingerprint(draft),
            purpose=self._purpose_from_fingerprint(draft),
            value_proposition=self._value_from_fingerprint(draft),
            prompt_version=draft.prompt_version,
        )
        context = self._context(self._load_records(generation, populate_existing=True))
        if build_context_fingerprint(context, generation) != draft.context_fingerprint:
            raise EmailDraftStaleContextError(_STALE)
        now = datetime.now(UTC)
        draft.status = target.value
        draft.reviewed_at = now
        if target is EmailDraftStatus.APPROVED:
            draft.approved_at = now
        else:
            draft.rejected_at = now
        self.session.flush()
        return self._read(draft)

    @staticmethod
    def _tone_from_fingerprint(draft: EmailDraft) -> EmailTone:
        try:
            return EmailTone(draft.generation_tone)
        except ValueError:
            raise EmailDraftIntegrityError(_INTEGRITY) from None

    @staticmethod
    def _language_from_draft(draft: EmailDraft) -> EmailLanguage:
        try:
            return EmailLanguage(draft.language)
        except ValueError:
            raise EmailDraftIntegrityError(_INTEGRITY) from None

    @staticmethod
    def _purpose_from_fingerprint(draft: EmailDraft) -> str:
        return draft.generation_purpose

    @staticmethod
    def _value_from_fingerprint(draft: EmailDraft) -> str | None:
        return draft.generation_value_proposition

    def _load_records(
        self, data: EmailDraftGenerationInput, *, populate_existing: bool = False
    ) -> EmailDraftSourceRecords:
        project = self.session.get(Project, data.project_id, populate_existing=populate_existing)
        company = self.session.get(Company, data.company_id, populate_existing=populate_existing)
        contact = (
            self.session.get(Contact, data.contact_id, populate_existing=populate_existing)
            if data.contact_id is not None
            else None
        )
        enrichment_statement = select(CompanyEnrichment).where(
            CompanyEnrichment.company_id == data.company_id
        )
        if populate_existing:
            enrichment_statement = enrichment_statement.execution_options(populate_existing=True)
        enrichment = self.session.scalar(enrichment_statement)
        lead = self.session.get(Lead, data.lead_id, populate_existing=populate_existing)
        task = self.session.get(Task, data.task_id, populate_existing=populate_existing)
        if project is None or company is None or lead is None or task is None:
            raise EmailDraftNotFoundError(_NOT_FOUND)
        if (
            company.project_id != project.id
            or lead.company_id != company.id
            or task.lead_id != lead.id
            or (
                contact is not None
                and (contact.company_id != company.id or lead.contact_id != contact.id)
            )
        ):
            raise EmailDraftScopeError(_SCOPE)
        if task.status not in {
            TaskLifecycleStatus.TODO.value,
            TaskLifecycleStatus.IN_PROGRESS.value,
        }:
            raise EmailDraftNotEligibleError(_NOT_ELIGIBLE)
        company_email = enrichment.email if enrichment is not None else None
        return EmailDraftSourceRecords(
            project=project,
            company=company,
            contact=contact,
            lead=lead,
            task=task,
            company_email=company_email,
        )

    @staticmethod
    def _context(records: EmailDraftSourceRecords) -> EmailPersonalizationContext:
        try:
            return build_email_personalization_context(records)
        except EmailDraftContextError as error:
            if "email" in str(error):
                raise EmailDraftMissingEmailError(_EMAIL) from None
            raise EmailDraftInvalidDataError(_INVALID) from None

    @staticmethod
    def _validated_generation(data: EmailDraftGenerationInput) -> EmailDraftGenerationInput:
        if type(data) is not EmailDraftGenerationInput:
            raise EmailDraftInvalidDataError(_INVALID)
        try:
            validated = EmailDraftGenerationInput(**data.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise EmailDraftInvalidDataError(_INVALID) from None
        if validated.prompt_version != EMAIL_DRAFT_PROMPT_VERSION:
            raise EmailDraftInvalidDataError(_INVALID)
        return validated

    @staticmethod
    def _validated_review(data: EmailDraftReviewInput) -> EmailDraftReviewInput:
        if type(data) is not EmailDraftReviewInput:
            raise EmailDraftInvalidDataError(_INVALID)
        try:
            return EmailDraftReviewInput(**data.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise EmailDraftConfirmationRequiredError(_CONFIRMATION) from None

    @staticmethod
    def _validated_result(
        result: object, data: EmailDraftGenerationInput
    ) -> EmailDraftGenerationResult:
        if type(result) is not EmailDraftGenerationResult:
            raise EmailDraftMalformedResultError(_MALFORMED)
        try:
            validated = EmailDraftGenerationResult(**result.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise EmailDraftMalformedResultError(_MALFORMED) from None
        if (
            validated.language is not data.language
            or validated.prompt_version != data.prompt_version
        ):
            raise EmailDraftMalformedResultError(_MALFORMED)
        return validated

    def _get_draft(
        self, data: EmailDraftReviewInput | EmailDraftScopeInput, *, for_update: bool
    ) -> EmailDraft:
        if for_update:
            draft = self.repository.get_for_scope_for_update(
                project_id=data.project_id,
                company_id=data.company_id,
                contact_id=data.contact_id,
                draft_id=data.draft_id,
            )
        else:
            draft = self.repository.get_for_scope(
                project_id=data.project_id,
                company_id=data.company_id,
                contact_id=data.contact_id,
                draft_id=data.draft_id,
            )
        if draft is None:
            raise EmailDraftNotFoundError(_NOT_FOUND)
        return draft

    @staticmethod
    def _read(draft: EmailDraft) -> EmailDraftRead:
        try:
            return EmailDraftRead.model_validate(draft)
        except (ValidationError, TypeError, ValueError):
            raise EmailDraftPersistenceError(_PERSISTENCE) from None

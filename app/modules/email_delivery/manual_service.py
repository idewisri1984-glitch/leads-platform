from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.contact.models import Contact
from app.modules.contact_discovery.normalization import normalize_discovered_email
from app.modules.email_draft.context import build_content_hash
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus, draft_is_sendable
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task, TaskLifecycleStatus

from .manual_models import ManualEmailSendRecord
from .manual_repository import ManualEmailSendRecordRepository
from .manual_schemas import (
    ConfirmedExternalManualEmailSendCommand,
    ConfirmedManualEmailSendCommand,
    ExternalManualEmailDraftScope,
    ManualEmailCopyPackage,
    ManualEmailDraftScope,
    ManualEmailSendRecordCreate,
    ManualOutreachStatus,
    ManualRecipientType,
)
from .outreach_mode import EmailDeliveryMode, claim_email_delivery_mode
from .repository import EmailDeliveryAttemptRepository

_INVALID = "Manual outreach command is invalid."
_CONFIRMATION = "Manual sent recording requires --confirm."
_NOT_FOUND = "Email draft was not found in the requested scope."
_NOT_APPROVED = "Email draft is not approved."
_STALE = "Email draft context is stale."
_ALREADY_AUTOMATIC = "Email draft already has an automatic delivery attempt."
_ALREADY_MANUAL = "Email draft is already recorded as manually sent."
_TRANSACTION = "Manual outreach requires a clean caller-owned transaction."
_PERSISTENCE = "Manual sent record could not be persisted."


class ManualOutreachError(Exception):
    pass


class ManualOutreachInvalidCommandError(ManualOutreachError):
    pass


class ManualOutreachConfirmationRequiredError(ManualOutreachError):
    pass


class ManualOutreachNotFoundError(ManualOutreachError):
    pass


class ManualOutreachCompanyScopedDraftError(ManualOutreachNotFoundError):
    pass


class ManualOutreachNotApprovedError(ManualOutreachError):
    pass


class ManualOutreachStaleContextError(ManualOutreachError):
    pass


class ManualOutreachAutomaticAttemptError(ManualOutreachError):
    pass


class ManualOutreachAlreadySentError(ManualOutreachError):
    pass


class ManualOutreachTransactionBoundaryError(ManualOutreachError):
    pass


class ManualOutreachPersistenceError(ManualOutreachError):
    pass


class ManualOutreachService:
    def __init__(
        self,
        session: Session,
        repository: ManualEmailSendRecordRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if repository.session is not session:
            raise ManualOutreachTransactionBoundaryError(_TRANSACTION)
        self.session = session
        self.repository = repository
        self.attempt_repository = EmailDeliveryAttemptRepository(session)
        self.clock = clock or (lambda: datetime.now(UTC))

    def export(self, scope: ManualEmailDraftScope) -> ManualEmailCopyPackage:
        data = self._validated_scope(scope)
        draft, company = self._load_authoritative_draft(data)
        if self.attempt_repository.get_by_email_draft_id(draft.id) is not None:
            raise ManualOutreachAutomaticAttemptError(_ALREADY_AUTOMATIC)
        record = self.repository.get_by_email_draft_id(draft.id)
        return self._package(draft, company, record)

    def mark_sent(self, command: ConfirmedManualEmailSendCommand) -> ManualEmailCopyPackage:
        if type(command) is not ConfirmedManualEmailSendCommand:
            raise ManualOutreachInvalidCommandError(_INVALID)
        try:
            data = ConfirmedManualEmailSendCommand(**command.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise ManualOutreachInvalidCommandError(_INVALID) from None
        if not data.confirmed:
            raise ManualOutreachConfirmationRequiredError(_CONFIRMATION)
        if self.session.in_transaction():
            raise ManualOutreachTransactionBoundaryError(_TRANSACTION)
        try:
            draft, company = self._load_authoritative_draft(data)
            if self.attempt_repository.get_by_email_draft_id(draft.id) is not None:
                raise ManualOutreachAutomaticAttemptError(_ALREADY_AUTOMATIC)
            if self.repository.get_by_email_draft_id(draft.id) is not None:
                raise ManualOutreachAlreadySentError(_ALREADY_MANUAL)
            if not claim_email_delivery_mode(
                self.session,
                email_draft_id=draft.id,
                mode=EmailDeliveryMode.MANUAL,
            ):
                if self.attempt_repository.get_by_email_draft_id(draft.id) is not None:
                    raise ManualOutreachAutomaticAttemptError(_ALREADY_AUTOMATIC)
                raise ManualOutreachAlreadySentError(_ALREADY_MANUAL)
            record = self.repository.create(
                ManualEmailSendRecordCreate(
                    project_id=draft.project_id,
                    company_id=draft.company_id,
                    contact_id=self._contact_id(draft),
                    email_draft_id=draft.id,
                    recipient_email=draft.recipient_email,
                    sent_at=self._utc(self.clock()),
                )
            )
            return self._package(draft, company, record)
        except ManualOutreachError:
            self.session.rollback()
            raise
        except IntegrityError:
            self.session.rollback()
            raise ManualOutreachAlreadySentError(_ALREADY_MANUAL) from None
        except SQLAlchemyError:
            self.session.rollback()
            raise ManualOutreachPersistenceError(_PERSISTENCE) from None

    def record_external_manual_send(
        self, command: ConfirmedExternalManualEmailSendCommand
    ) -> ManualEmailCopyPackage:
        if type(command) is not ConfirmedExternalManualEmailSendCommand:
            raise ManualOutreachInvalidCommandError(_INVALID)
        try:
            data = ConfirmedExternalManualEmailSendCommand(**command.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise ManualOutreachInvalidCommandError(_INVALID) from None
        if not data.confirmed:
            raise ManualOutreachConfirmationRequiredError(_CONFIRMATION)
        if self.session.in_transaction():
            raise ManualOutreachTransactionBoundaryError(_TRANSACTION)
        try:
            draft, company = self._load_external_draft(data)
            if self.attempt_repository.get_by_email_draft_id(draft.id) is not None:
                raise ManualOutreachAutomaticAttemptError(_ALREADY_AUTOMATIC)
            existing = self.repository.get_by_email_draft_id(draft.id)
            if existing is not None:
                return self._package(draft, company, existing)

            recorded_at = self._utc(self.clock())
            if not claim_email_delivery_mode(
                self.session,
                email_draft_id=draft.id,
                mode=EmailDeliveryMode.MANUAL,
            ):
                self.session.expire_all()
                if self.attempt_repository.get_by_email_draft_id(draft.id) is not None:
                    raise ManualOutreachAutomaticAttemptError(_ALREADY_AUTOMATIC)
                existing = self.repository.get_by_email_draft_id(draft.id)
                if existing is not None:
                    return self._package(draft, company, existing)
                raise ManualOutreachAlreadySentError(_ALREADY_MANUAL)

            record = self.repository.create(
                ManualEmailSendRecordCreate(
                    project_id=draft.project_id,
                    company_id=draft.company_id,
                    contact_id=draft.contact_id,
                    email_draft_id=draft.id,
                    recipient_email=draft.recipient_email,
                    sent_at=recorded_at,
                )
            )
            return self._package(draft, company, record)
        except ManualOutreachError:
            self.session.rollback()
            raise
        except IntegrityError:
            self.session.rollback()
            raise ManualOutreachAlreadySentError(_ALREADY_MANUAL) from None
        except (SQLAlchemyError, ValueError):
            self.session.rollback()
            raise ManualOutreachPersistenceError(_PERSISTENCE) from None

    @staticmethod
    def _validated_scope(scope: object) -> ManualEmailDraftScope:
        if type(scope) is ManualEmailDraftScope or type(scope) is ConfirmedManualEmailSendCommand:
            values = scope
        else:
            raise ManualOutreachInvalidCommandError(_INVALID)
        try:
            return ManualEmailDraftScope(
                project_id=values.project_id,
                company_id=values.company_id,
                contact_id=values.contact_id,
                email_draft_id=values.email_draft_id,
            )
        except (ValidationError, TypeError, ValueError):
            raise ManualOutreachInvalidCommandError(_INVALID) from None

    def _load_authoritative_draft(self, data: ManualEmailDraftScope) -> tuple[EmailDraft, Company]:
        self.session.expire_all()
        draft = self.session.get(EmailDraft, data.email_draft_id, populate_existing=True)
        if draft is not None and not draft_is_sendable(draft):
            raise ManualOutreachCompanyScopedDraftError("COMPANY_SCOPED_DRAFT_NOT_SENDABLE")
        if (
            draft is None
            or draft.project_id != data.project_id
            or draft.company_id != data.company_id
            or draft.contact_id != data.contact_id
        ):
            raise ManualOutreachNotFoundError(_NOT_FOUND)
        project = self.session.get(Project, draft.project_id, populate_existing=True)
        company = self.session.get(Company, draft.company_id, populate_existing=True)
        contact = self.session.get(Contact, draft.contact_id, populate_existing=True)
        lead = self.session.get(Lead, draft.lead_id, populate_existing=True)
        task = self.session.get(Task, draft.task_id, populate_existing=True)
        if project is None or company is None or contact is None or lead is None or task is None:
            raise ManualOutreachNotFoundError(_NOT_FOUND)
        if (
            company.project_id != project.id
            or contact.company_id != company.id
            or lead.company_id != company.id
            or lead.contact_id != contact.id
            or task.lead_id != lead.id
        ):
            raise ManualOutreachNotFoundError(_NOT_FOUND)
        if task.status not in {
            TaskLifecycleStatus.TODO.value,
            TaskLifecycleStatus.IN_PROGRESS.value,
        }:
            raise ManualOutreachStaleContextError(_STALE)
        if draft.status != EmailDraftStatus.APPROVED.value:
            raise ManualOutreachNotApprovedError(_NOT_APPROVED)
        try:
            current_email = (
                normalize_discovered_email(contact.email) if type(contact.email) is str else None
            )
        except (TypeError, ValueError):
            current_email = None
        if current_email is None or current_email != draft.recipient_email:
            raise ManualOutreachStaleContextError(_STALE)
        expected_hash = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=draft.text_body,
            prompt_version=draft.prompt_version,
        )
        if expected_hash != draft.content_hash:
            raise ManualOutreachStaleContextError(_STALE)
        return draft, company

    def _load_external_draft(
        self, data: ExternalManualEmailDraftScope
    ) -> tuple[EmailDraft, Company]:
        self.session.expire_all()
        draft = self.session.get(EmailDraft, data.email_draft_id, populate_existing=True)
        if (
            draft is None
            or draft.project_id != data.project_id
            or draft.company_id != data.company_id
        ):
            raise ManualOutreachNotFoundError(_NOT_FOUND)
        project = self.session.get(Project, draft.project_id, populate_existing=True)
        company = self.session.get(Company, draft.company_id, populate_existing=True)
        lead = self.session.get(Lead, draft.lead_id, populate_existing=True)
        task = self.session.get(Task, draft.task_id, populate_existing=True)
        if project is None or company is None or lead is None or task is None:
            raise ManualOutreachNotFoundError(_NOT_FOUND)
        if (
            company.project_id != project.id
            or lead.company_id != company.id
            or task.lead_id != lead.id
        ):
            raise ManualOutreachNotFoundError(_NOT_FOUND)

        if draft.contact_id is None:
            if lead.contact_id is not None:
                raise ManualOutreachNotFoundError(_NOT_FOUND)
            enrichment = self.session.scalar(
                select(CompanyEnrichment).where(CompanyEnrichment.company_id == company.id)
            )
            canonical_email = self._normalized_email(
                enrichment.email if enrichment is not None else None
            )
        else:
            contact = self.session.get(Contact, draft.contact_id, populate_existing=True)
            if contact is None or contact.company_id != company.id or lead.contact_id != contact.id:
                raise ManualOutreachNotFoundError(_NOT_FOUND)
            canonical_email = self._normalized_email(contact.email)

        if canonical_email is None or canonical_email != draft.recipient_email:
            raise ManualOutreachStaleContextError(_STALE)
        if task.status not in {
            TaskLifecycleStatus.TODO.value,
            TaskLifecycleStatus.IN_PROGRESS.value,
        }:
            raise ManualOutreachStaleContextError(_STALE)
        if draft.status not in {
            EmailDraftStatus.DRAFT.value,
            EmailDraftStatus.APPROVED.value,
        }:
            raise ManualOutreachNotApprovedError(_NOT_APPROVED)
        expected_hash = build_content_hash(
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            text_body=draft.text_body,
            prompt_version=draft.prompt_version,
        )
        if expected_hash != draft.content_hash:
            raise ManualOutreachStaleContextError(_STALE)
        return draft, company

    @staticmethod
    def _normalized_email(value: object) -> str | None:
        if type(value) is not str:
            return None
        try:
            return normalize_discovered_email(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _contact_id(draft: EmailDraft) -> int:
        if draft.contact_id is None:
            raise ManualOutreachCompanyScopedDraftError("COMPANY_SCOPED_DRAFT_NOT_SENDABLE")
        return draft.contact_id

    @staticmethod
    def _package(
        draft: EmailDraft,
        company: Company,
        record: ManualEmailSendRecord | None,
    ) -> ManualEmailCopyPackage:
        return ManualEmailCopyPackage(
            project_id=draft.project_id,
            company_id=draft.company_id,
            contact_id=draft.contact_id,
            lead_id=draft.lead_id,
            task_id=draft.task_id,
            email_draft_id=draft.id,
            recipient_email=draft.recipient_email,
            recipient_name=draft.recipient_name,
            company_name=company.name,
            subject=draft.subject,
            text_body=draft.text_body,
            draft_status=draft.status,
            recipient_type=(
                ManualRecipientType.PERSON
                if draft.contact_id is not None
                else ManualRecipientType.COMPANY
            ),
            outreach_status=(
                ManualOutreachStatus.MANUALLY_SENT
                if record is not None
                else ManualOutreachStatus.READY_FOR_MANUAL_SEND
            ),
            content_hash=draft.content_hash,
            manual_send_record_id=record.id if record is not None else None,
            sent_at=record.sent_at if record is not None else None,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ManualOutreachInvalidCommandError(_INVALID)
        return value.astimezone(UTC)

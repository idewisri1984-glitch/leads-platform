from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from hmac import compare_digest
from math import isfinite
from typing import Protocol, cast

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from ..contact_discovery.candidate_promotion import (
    ContactDiscoveryCandidateNotEligibleError,
    ContactDiscoveryCandidatePromotionConsistencyError,
    ContactDiscoveryCandidatePromotionInvalidDataError,
    ContactDiscoveryCandidatePromotionNotFoundError,
    ContactDiscoveryCandidatePromotionService,
)
from ..contact_discovery.candidate_promotion_schemas import (
    ContactDiscoveryCandidatePromotionResult,
)
from ..contact_discovery.candidate_review import (
    ContactDiscoveryCandidateReviewNotFoundError,
    ContactDiscoveryCandidateReviewService,
    ContactDiscoveryCandidateTransitionError,
)
from ..contact_discovery.candidate_review_schemas import (
    ContactDiscoveryCandidateReviewResult,
)
from ..contact_discovery.models import (
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from ..contact_discovery.normalization import normalize_discovered_email
from .contact_apply_schemas import AgentContactApplyInput, AgentContactApplyResult
from .contact_plan_contract import build_contact_plan_proposals, normalize_contact_plan_text
from .contact_plan_handoff import (
    build_agent_contact_plan_handoff_token,
    canonicalize_handoff_datetime,
)

_INVALID = "Agent contact apply data is invalid."
_CONFIRMATION = "Agent contact apply requires --yes."
_NOT_FOUND = "Agent contact apply target was not found."
_STALE = "Agent contact apply handoff is stale."
_NOT_ELIGIBLE = "Agent contact apply candidate is not eligible."
_INCONSISTENT = "Agent contact apply state is inconsistent."
_CONFLICT = "Agent contact apply found conflicting CRM state."
_PERSISTENCE = "Agent contact apply could not be persisted."
_INTERNAL = "Agent contact apply failed."


class AgentContactApplyError(ValueError):
    pass


class AgentContactApplyInvalidDataError(AgentContactApplyError):
    pass


class AgentContactApplyConfirmationRequiredError(AgentContactApplyError):
    pass


class AgentContactApplyNotFoundError(AgentContactApplyError):
    pass


class AgentContactApplyStaleHandoffError(AgentContactApplyError):
    pass


class AgentContactApplyNotEligibleError(AgentContactApplyError):
    pass


class AgentContactApplyConsistencyError(AgentContactApplyError):
    pass


class AgentContactApplyConflictError(AgentContactApplyError):
    pass


class AgentContactApplyPersistenceError(AgentContactApplyError):
    pass


class AgentContactApplyInternalError(AgentContactApplyError):
    pass


class _CompanyRecord(Protocol):
    id: object
    project_id: object
    name: object
    website: object


class _StateRecord(Protocol):
    company_id: object
    provider: object
    discovery_status: object
    checked_at: object
    last_error: object


class _CandidateRecord(Protocol):
    id: object
    company_id: object
    promoted_contact_id: object
    name: object
    title: object
    email: object
    normalized_email: object
    phone: object
    source_url: object
    source_type: object
    confidence: object
    discovery_status: object
    deduplication_key: object


class _ContactRecord(Protocol):
    id: object
    company_id: object


class _LeadRecord(Protocol):
    id: object
    company_id: object
    contact_id: object
    status: object
    source: object
    notes: object


class _TaskRecord(Protocol):
    id: object
    lead_id: object
    title: object
    description: object
    status: object
    due_at: object


class _CompanyRepository(Protocol):
    def get_for_project(self, project_id: int, company_id: int) -> _CompanyRecord | None: ...


class _ContactRepository(Protocol):
    def acquire_promotion_scope(self, company_id: int) -> None: ...

    def get_for_company(self, company_id: int, contact_id: int) -> _ContactRecord | None: ...

    def find_promotion_duplicate_by_email(
        self, company_id: int, normalized_email: str
    ) -> _ContactRecord | None: ...


class _DiscoveryRepository(Protocol):
    def get_state_for_update(self, company_id: int) -> _StateRecord | None: ...

    def get_candidate_for_promotion(
        self, company_id: int, candidate_id: int
    ) -> _CandidateRecord | None: ...


class _LeadRepository(Protocol):
    def get_by_contact(self, contact_id: int) -> list[_LeadRecord]: ...

    def create_for_contact(
        self, *, company_id: int, contact_id: int, status: str, source: str | None
    ) -> _LeadRecord: ...


class _TaskRepository(Protocol):
    def get_by_lead(self, lead_id: int) -> list[_TaskRecord]: ...

    def create_for_lead(
        self, *, lead_id: int, title: str, description: str | None
    ) -> _TaskRecord: ...


@dataclass(frozen=True, slots=True)
class _CompanySnapshot:
    id: int
    project_id: int
    name: str
    website: str


@dataclass(frozen=True, slots=True)
class _StateSnapshot:
    company_id: int
    provider: str
    status: ContactDiscoveryStatus
    checked_at: object
    last_error: str | None


@dataclass(frozen=True, slots=True)
class _CandidateSnapshot:
    id: int
    company_id: int
    promoted_contact_id: int | None
    name: str
    title: str | None
    email: str | None
    normalized_email: str | None
    phone: str | None
    source_url: str | None
    source_type: ContactDiscoverySourceType
    confidence: float
    status: ContactDiscoveryCandidateStatus
    deduplication_key: str


def _repository_call[T](operation: Callable[[], T]) -> T:
    conflict = False
    failed = False
    value: T | None = None
    try:
        value = operation()
    except IntegrityError:
        conflict = True
    except Exception:
        failed = True
    if conflict:
        raise AgentContactApplyConflictError(_CONFLICT)
    if failed:
        raise AgentContactApplyPersistenceError(_PERSISTENCE)
    return cast(T, value)


class AgentContactApplyService:
    def __init__(
        self,
        *,
        company_repository: _CompanyRepository,
        contact_repository: _ContactRepository,
        discovery_repository: _DiscoveryRepository,
        review_service: ContactDiscoveryCandidateReviewService,
        promotion_service: ContactDiscoveryCandidatePromotionService,
        lead_repository: _LeadRepository,
        task_repository: _TaskRepository,
    ) -> None:
        self.company_repository = company_repository
        self.contact_repository = contact_repository
        self.discovery_repository = discovery_repository
        self.review_service = review_service
        self.promotion_service = promotion_service
        self.lead_repository = lead_repository
        self.task_repository = task_repository

    def apply(self, apply_input: AgentContactApplyInput) -> AgentContactApplyResult:
        data = self._validate_input(apply_input)
        try:
            return self._apply(data)
        except AgentContactApplyError:
            raise
        except Exception:
            raise AgentContactApplyInternalError(_INTERNAL) from None

    def _apply(self, data: AgentContactApplyInput) -> AgentContactApplyResult:
        self._acquire_scope(data.company_id)
        company_record = _repository_call(
            lambda: self.company_repository.get_for_project(data.project_id, data.company_id)
        )
        if company_record is None:
            raise AgentContactApplyNotFoundError(_NOT_FOUND)
        company = self._snapshot_company(company_record, data)

        state_record = _repository_call(
            lambda: self.discovery_repository.get_state_for_update(data.company_id)
        )
        if state_record is None:
            raise AgentContactApplyNotFoundError(_NOT_FOUND)
        state = self._snapshot_state(state_record, data.company_id)

        candidate_record = _repository_call(
            lambda: self.discovery_repository.get_candidate_for_promotion(
                data.company_id, data.candidate_id
            )
        )
        if candidate_record is None:
            raise AgentContactApplyNotFoundError(_NOT_FOUND)
        candidate = self._snapshot_candidate(candidate_record, data)
        proposals = build_contact_plan_proposals(
            company_name=company.name,
            candidate_name=candidate.name,
            candidate_title=candidate.title,
            goal=data.goal,
        )
        try:
            expected_token = build_agent_contact_plan_handoff_token(
                project_id=data.project_id,
                company_id=company.id,
                company_name=company.name,
                company_website=company.website,
                goal=data.goal,
                provider_name=state.provider,
                discovery_checked_at=state.checked_at,
                candidate_id=candidate.id,
                candidate_deduplication_key=candidate.deduplication_key,
                candidate_name=candidate.name,
                candidate_title=candidate.title,
                candidate_email=candidate.email,
                candidate_phone=candidate.phone,
                candidate_source_url=candidate.source_url,
                candidate_source_type=candidate.source_type,
                candidate_confidence=candidate.confidence,
                proposed_lead_title=proposals.lead_title,
                proposed_task_title=proposals.task_title,
                proposed_task_description=proposals.task_description,
            )
        except (TypeError, ValueError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if not compare_digest(expected_token, data.handoff_token):
            raise AgentContactApplyStaleHandoffError(_STALE)
        if state.status not in {ContactDiscoveryStatus.SUCCEEDED, ContactDiscoveryStatus.PARTIAL}:
            raise AgentContactApplyNotEligibleError(_NOT_ELIGIBLE)
        if candidate.status is ContactDiscoveryCandidateStatus.REJECTED:
            raise AgentContactApplyNotEligibleError(_NOT_ELIGIBLE)

        preexisting_contact_id = self._preexisting_contact_id(candidate, data.company_id)
        before = candidate.status
        reviewed = False
        if before is ContactDiscoveryCandidateStatus.DISCOVERED:
            review = self._review(data)
            self._validate_review(review, data)
            reviewed = True
        promotion = self._promote(data)
        promotion = self._validate_promotion(promotion, data, before)

        final_record = _repository_call(
            lambda: self.discovery_repository.get_candidate_for_promotion(
                data.company_id, data.candidate_id
            )
        )
        if final_record is None:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        final_candidate = self._snapshot_candidate(final_record, data)
        if (
            final_candidate.status is not ContactDiscoveryCandidateStatus.PROMOTED
            or final_candidate.promoted_contact_id != promotion.contact_id
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        contact_record = _repository_call(
            lambda: self.contact_repository.get_for_company(data.company_id, promotion.contact_id)
        )
        contact_id = self._validate_contact(contact_record, data.company_id, promotion.contact_id)
        contact_created = preexisting_contact_id is None
        if (
            preexisting_contact_id is not None and preexisting_contact_id != contact_id
        ) or promotion.created_contact is not contact_created:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        lead, lead_created = self._materialize_lead(data.company_id, contact_id)
        lead_id = lead.id
        if type(lead_id) is not int:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        task, task_created = self._materialize_task(
            lead_id, proposals.task_title, proposals.task_description
        )
        promoted = before is not ContactDiscoveryCandidateStatus.PROMOTED
        try:
            return AgentContactApplyResult(
                project_id=data.project_id,
                company_id=data.company_id,
                candidate_id=data.candidate_id,
                contact_id=contact_id,
                lead_id=lead_id,
                task_id=task.id,
                candidate_status_before=before,
                candidate_status_after=ContactDiscoveryCandidateStatus.PROMOTED,
                candidate_reviewed=reviewed,
                candidate_promoted=promoted,
                contact_created=contact_created,
                contact_reused=not contact_created,
                lead_created=lead_created,
                lead_reused=not lead_created,
                task_created=task_created,
                task_reused=not task_created,
                staging_mutated=reviewed or promoted,
                crm_mutated=contact_created or lead_created or task_created,
                network_call_count=0,
                contact_mutation_count=int(contact_created),
                lead_mutation_count=int(lead_created),
                task_mutation_count=int(task_created),
                handoff_verified=True,
                human_confirmation_required=True,
                human_confirmation_received=True,
            )
        except (TypeError, ValueError, ValidationError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None

    @staticmethod
    def _validate_input(value: AgentContactApplyInput) -> AgentContactApplyInput:
        if type(value) is not AgentContactApplyInput:
            raise AgentContactApplyInvalidDataError(_INVALID)
        try:
            confirmed = value.confirmed
        except AttributeError:
            raise AgentContactApplyConfirmationRequiredError(_CONFIRMATION) from None
        if type(confirmed) is not bool or confirmed is not True:
            raise AgentContactApplyConfirmationRequiredError(_CONFIRMATION)
        try:
            return AgentContactApplyInput(
                project_id=value.project_id,
                company_id=value.company_id,
                candidate_id=value.candidate_id,
                goal=value.goal,
                handoff_token=value.handoff_token,
                confirmed=confirmed,
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise AgentContactApplyInvalidDataError(_INVALID) from None

    def _acquire_scope(self, company_id: int) -> None:
        try:
            self.contact_repository.acquire_promotion_scope(company_id)
        except IntegrityError:
            raise AgentContactApplyConflictError(_CONFLICT) from None
        except (TypeError, ValueError):
            raise AgentContactApplyNotFoundError(_NOT_FOUND) from None
        except Exception:
            raise AgentContactApplyPersistenceError(_PERSISTENCE) from None

    @staticmethod
    def _snapshot_company(record: _CompanyRecord, data: AgentContactApplyInput) -> _CompanySnapshot:
        try:
            company_id = record.id
            project_id = record.project_id
            name = normalize_contact_plan_text(record.name, required=True)
            website = normalize_contact_plan_text(record.website, required=True)
        except (AttributeError, TypeError, ValueError, UnicodeEncodeError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if (
            type(company_id) is not int
            or type(project_id) is not int
            or company_id != data.company_id
            or project_id != data.project_id
            or name is None
            or website is None
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return _CompanySnapshot(company_id, project_id, name, website)

    @classmethod
    def _snapshot_state(cls, record: _StateRecord, company_id: int) -> _StateSnapshot:
        try:
            stored_company_id = record.company_id
            provider = record.provider
            status = cls._strict_enum(ContactDiscoveryStatus, record.discovery_status)
            checked_at = record.checked_at
            last_error = record.last_error
            canonicalize_handoff_datetime(checked_at)
        except (AttributeError, TypeError, ValueError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if (
            type(stored_company_id) is not int
            or stored_company_id != company_id
            or type(provider) is not str
            or not provider.strip()
            or len(provider) > 100
            or provider != " ".join(provider.split())
            or (last_error is not None and type(last_error) is not str)
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return _StateSnapshot(stored_company_id, provider, status, checked_at, last_error)

    @classmethod
    def _snapshot_candidate(
        cls, record: _CandidateRecord, data: AgentContactApplyInput
    ) -> _CandidateSnapshot:
        try:
            candidate_id = record.id
            company_id = record.company_id
            promoted_contact_id = record.promoted_contact_id
            name = normalize_contact_plan_text(record.name, required=True)
            title = normalize_contact_plan_text(record.title)
            email = normalize_contact_plan_text(record.email)
            normalized_email = record.normalized_email
            phone = normalize_contact_plan_text(record.phone)
            source_url = normalize_contact_plan_text(record.source_url)
            source_type = cls._strict_enum(ContactDiscoverySourceType, record.source_type)
            stored_confidence = record.confidence
            status = cls._strict_enum(ContactDiscoveryCandidateStatus, record.discovery_status)
            deduplication_key = record.deduplication_key
        except (AttributeError, TypeError, ValueError, UnicodeEncodeError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if (
            type(candidate_id) is not int
            or candidate_id != data.candidate_id
            or type(company_id) is not int
            or company_id != data.company_id
            or (
                promoted_contact_id is not None
                and (type(promoted_contact_id) is not int or promoted_contact_id <= 0)
            )
            or name is None
            or (normalized_email is not None and type(normalized_email) is not str)
            or type(stored_confidence) is not int
            or not 0 <= stored_confidence <= 100
            or type(deduplication_key) is not str
            or not deduplication_key.strip()
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        if status is ContactDiscoveryCandidateStatus.PROMOTED:
            if promoted_contact_id is None:
                raise AgentContactApplyConsistencyError(_INCONSISTENT)
        elif promoted_contact_id is not None:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        confidence = float(stored_confidence) / 100.0
        if not isfinite(confidence):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return _CandidateSnapshot(
            candidate_id,
            company_id,
            promoted_contact_id,
            name,
            title,
            email,
            normalized_email,
            phone,
            source_url,
            source_type,
            confidence,
            status,
            deduplication_key,
        )

    @staticmethod
    def _strict_enum[T: Enum](enum_type: type[T], value: object) -> T:
        if type(value) is enum_type:
            return value
        if type(value) is not str:
            raise ValueError
        return enum_type(value)

    def _review(self, data: AgentContactApplyInput) -> ContactDiscoveryCandidateReviewResult:
        try:
            return self.review_service.mark_reviewed(data.company_id, data.candidate_id)
        except IntegrityError:
            raise AgentContactApplyConflictError(_CONFLICT) from None
        except (
            ContactDiscoveryCandidateReviewNotFoundError,
            ContactDiscoveryCandidateTransitionError,
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        except Exception:
            raise AgentContactApplyPersistenceError(_PERSISTENCE) from None

    @staticmethod
    def _validate_review(
        raw: ContactDiscoveryCandidateReviewResult, data: AgentContactApplyInput
    ) -> None:
        try:
            if type(raw) is not ContactDiscoveryCandidateReviewResult:
                raise TypeError
            result = ContactDiscoveryCandidateReviewResult.model_validate(raw.model_dump())
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if (
            result.candidate.id != data.candidate_id
            or result.candidate.company_id != data.company_id
            or result.previous_status is not ContactDiscoveryCandidateStatus.DISCOVERED
            or result.current_status is not ContactDiscoveryCandidateStatus.REVIEWED
            or result.candidate.discovery_status is not ContactDiscoveryCandidateStatus.REVIEWED
            or result.changed is not True
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)

    def _promote(self, data: AgentContactApplyInput) -> ContactDiscoveryCandidatePromotionResult:
        try:
            return self.promotion_service.promote(data.company_id, data.candidate_id)
        except IntegrityError:
            raise AgentContactApplyConflictError(_CONFLICT) from None
        except ContactDiscoveryCandidatePromotionNotFoundError:
            raise AgentContactApplyNotFoundError(_NOT_FOUND) from None
        except ContactDiscoveryCandidateNotEligibleError:
            raise AgentContactApplyNotEligibleError(_NOT_ELIGIBLE) from None
        except (
            ContactDiscoveryCandidatePromotionInvalidDataError,
            ContactDiscoveryCandidatePromotionConsistencyError,
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        except Exception:
            raise AgentContactApplyPersistenceError(_PERSISTENCE) from None

    def _preexisting_contact_id(self, candidate: _CandidateSnapshot, company_id: int) -> int | None:
        if candidate.status is ContactDiscoveryCandidateStatus.PROMOTED:
            contact_id = candidate.promoted_contact_id
            if contact_id is None:
                raise AgentContactApplyConsistencyError(_INCONSISTENT)
            valid_contact_id = contact_id
            record = _repository_call(
                lambda: self.contact_repository.get_for_company(company_id, valid_contact_id)
            )
            return self._validate_contact(record, company_id, valid_contact_id)

        canonical_email = self._canonical_candidate_email(candidate)
        if canonical_email is None:
            return None
        record = _repository_call(
            lambda: self.contact_repository.find_promotion_duplicate_by_email(
                company_id, canonical_email
            )
        )
        if record is None:
            return None
        try:
            record_contact_id = record.id
        except AttributeError:
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if type(record_contact_id) is not int or record_contact_id <= 0:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return self._validate_contact(record, company_id, record_contact_id)

    @staticmethod
    def _canonical_candidate_email(candidate: _CandidateSnapshot) -> str | None:
        try:
            raw_email = (
                normalize_discovered_email(candidate.email) if candidate.email is not None else None
            )
            stored_email = (
                normalize_discovered_email(candidate.normalized_email)
                if candidate.normalized_email is not None
                else None
            )
        except (TypeError, ValueError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        if (
            (candidate.email is not None and raw_email is None)
            or (
                candidate.normalized_email is not None
                and stored_email != candidate.normalized_email
            )
            or (raw_email is not None and stored_email is not None and raw_email != stored_email)
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return stored_email or raw_email

    @staticmethod
    def _validate_promotion(
        raw: ContactDiscoveryCandidatePromotionResult,
        data: AgentContactApplyInput,
        before: ContactDiscoveryCandidateStatus,
    ) -> ContactDiscoveryCandidatePromotionResult:
        try:
            if type(raw) is not ContactDiscoveryCandidatePromotionResult:
                raise TypeError
            result = ContactDiscoveryCandidatePromotionResult.model_validate(raw.model_dump())
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise AgentContactApplyConsistencyError(_INCONSISTENT) from None
        expected_previous = (
            ContactDiscoveryCandidateStatus.PROMOTED
            if before is ContactDiscoveryCandidateStatus.PROMOTED
            else ContactDiscoveryCandidateStatus.REVIEWED
        )
        expected_changed = before is not ContactDiscoveryCandidateStatus.PROMOTED
        if (
            result.candidate_id != data.candidate_id
            or result.company_id != data.company_id
            or result.previous_status is not expected_previous
            or result.current_status is not ContactDiscoveryCandidateStatus.PROMOTED
            or result.changed is not expected_changed
        ):
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return result

    @staticmethod
    def _validate_contact(record: _ContactRecord | None, company_id: int, contact_id: int) -> int:
        try:
            valid = (
                record is not None
                and type(record.id) is int
                and record.id == contact_id
                and type(record.company_id) is int
                and record.company_id == company_id
            )
        except AttributeError:
            valid = False
        if not valid:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        return contact_id

    def _materialize_lead(self, company_id: int, contact_id: int) -> tuple[_LeadRecord, bool]:
        existing = _repository_call(lambda: self.lead_repository.get_by_contact(contact_id))
        if type(existing) is not list:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        if len(existing) > 1:
            raise AgentContactApplyConflictError(_CONFLICT)
        created = not existing
        lead = (
            _repository_call(
                lambda: self.lead_repository.create_for_contact(
                    company_id=company_id, contact_id=contact_id, status="NEW", source=None
                )
            )
            if created
            else existing[0]
        )
        self._validate_lead(lead, company_id, contact_id)
        return lead, created

    @staticmethod
    def _validate_lead(lead: _LeadRecord, company_id: int, contact_id: int) -> None:
        try:
            valid = (
                type(lead.id) is int
                and lead.id > 0
                and type(lead.company_id) is int
                and lead.company_id == company_id
                and type(lead.contact_id) is int
                and lead.contact_id == contact_id
                and type(lead.status) is str
                and lead.status == "NEW"
                and lead.source is None
                and lead.notes is None
            )
        except AttributeError:
            valid = False
        if not valid:
            raise AgentContactApplyConflictError(_CONFLICT)

    def _materialize_task(
        self, lead_id: int, title: str, description: str
    ) -> tuple[_TaskRecord, bool]:
        existing = _repository_call(lambda: self.task_repository.get_by_lead(lead_id))
        if type(existing) is not list:
            raise AgentContactApplyConsistencyError(_INCONSISTENT)
        if len(existing) > 1:
            raise AgentContactApplyConflictError(_CONFLICT)
        created = not existing
        task = (
            _repository_call(
                lambda: self.task_repository.create_for_lead(
                    lead_id=lead_id, title=title, description=description
                )
            )
            if created
            else existing[0]
        )
        self._validate_task(task, lead_id, title, description)
        return task, created

    @staticmethod
    def _validate_task(task: _TaskRecord, lead_id: int, title: str, description: str) -> None:
        try:
            valid = (
                type(task.id) is int
                and task.id > 0
                and type(task.lead_id) is int
                and task.lead_id == lead_id
                and type(task.title) is str
                and task.title == title
                and type(task.description) is str
                and task.description == description
                and type(task.status) is str
                and task.status == "TODO"
                and task.due_at is None
            )
        except AttributeError:
            valid = False
        if not valid:
            raise AgentContactApplyConflictError(_CONFLICT)


__all__ = [
    "AgentContactApplyConflictError",
    "AgentContactApplyConfirmationRequiredError",
    "AgentContactApplyConsistencyError",
    "AgentContactApplyError",
    "AgentContactApplyInternalError",
    "AgentContactApplyInvalidDataError",
    "AgentContactApplyNotEligibleError",
    "AgentContactApplyNotFoundError",
    "AgentContactApplyPersistenceError",
    "AgentContactApplyService",
    "AgentContactApplyStaleHandoffError",
]

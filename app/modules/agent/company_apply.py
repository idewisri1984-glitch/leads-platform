from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.modules.company_discovery import (
    CompanyDiscoveryCandidateNotEligibleError,
    CompanyDiscoveryCandidatePromotionConsistencyError,
    CompanyDiscoveryCandidatePromotionInvalidDataError,
    CompanyDiscoveryCandidatePromotionNotFoundError,
    CompanyDiscoveryCandidatePromotionResult,
    CompanyDiscoveryCandidateReviewNotFoundError,
    CompanyDiscoveryCandidateReviewResult,
    CompanyDiscoveryCandidateTransitionError,
)
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateRead

from .company_apply_schemas import AgentCompanyApplyInput, AgentCompanyApplyResult

_INVALID = "Agent company apply data is invalid."
_CONFIRMATION = "Agent company apply requires --yes."
_NOT_FOUND = "Agent company apply target was not found."
_STALE = "Agent company apply handoff is stale."
_RUN_INELIGIBLE = "Agent company apply run is not eligible."
_CANDIDATE_INELIGIBLE = "Agent company apply candidate is not eligible."
_INCONSISTENT = "Agent company apply state is inconsistent."
_COMPANY_INVALID = "Agent company apply Company state is invalid."
_CONFLICT = "Agent company apply persistence conflict."
_PERSISTENCE = "Agent company apply could not be persisted."
_INTERNAL = "Agent company apply failed."


class AgentCompanyApplyError(ValueError):
    pass


class AgentCompanyApplyInvalidDataError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyConfirmationRequiredError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyNotFoundError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyStaleHandoffError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyNotEligibleError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyConsistencyError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyConflictError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyPersistenceError(AgentCompanyApplyError):
    pass


class AgentCompanyApplyInternalError(AgentCompanyApplyError):
    pass


class _RunRecord(Protocol):
    id: object
    project_id: object
    run_status: object


class _CandidateRecord(Protocol):
    id: object
    project_id: object
    first_seen_run_id: object
    last_seen_run_id: object
    name: object
    website: object
    country_code: object
    candidate_status: object
    promoted_company_id: object


class _CompanyRecord(Protocol):
    id: object
    project_id: object


class _StagingRepository(Protocol):
    def get_run(self, run_id: int) -> _RunRecord | None: ...

    def get_candidate_for_promotion(
        self, project_id: int, candidate_id: int
    ) -> _CandidateRecord | None: ...


class _CompanyRepository(Protocol):
    def acquire_promotion_scope(self, project_id: int) -> None: ...

    def get_for_project(self, project_id: int, company_id: int) -> _CompanyRecord | None: ...


class _ReviewService(Protocol):
    def mark_reviewed(
        self, project_id: int, candidate_id: int
    ) -> CompanyDiscoveryCandidateReviewResult: ...


class _PromotionService(Protocol):
    def promote(
        self, project_id: int, candidate_id: int
    ) -> CompanyDiscoveryCandidatePromotionResult: ...


@dataclass(frozen=True, slots=True)
class _RunSnapshot:
    id: object
    project_id: object
    run_status: object


@dataclass(frozen=True, slots=True)
class _CandidateSnapshot:
    id: object
    project_id: object
    first_seen_run_id: object
    last_seen_run_id: object
    name: object
    website: object
    country_code: object
    candidate_status: object
    promoted_company_id: object


@dataclass(frozen=True, slots=True)
class _CompanySnapshot:
    id: object
    project_id: object


def _translated_call[T](operation: Callable[[], T]) -> T:
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
        raise AgentCompanyApplyConflictError(_CONFLICT)
    if failed:
        raise AgentCompanyApplyPersistenceError(_PERSISTENCE)
    return cast(T, value)


class AgentCompanyApplyService:
    def __init__(
        self,
        *,
        staging_repository: _StagingRepository,
        company_repository: _CompanyRepository,
        review_service: _ReviewService,
        promotion_service: _PromotionService,
    ) -> None:
        self.staging_repository = staging_repository
        self.company_repository = company_repository
        self.review_service = review_service
        self.promotion_service = promotion_service

    def apply(self, apply_input: AgentCompanyApplyInput) -> AgentCompanyApplyResult:
        data = self._validate_input(apply_input)
        self._acquire_project(data.project_id)

        run_record = _translated_call(
            lambda: self.staging_repository.get_run(data.discovery_run_id)
        )
        if run_record is None:
            raise AgentCompanyApplyNotFoundError(_NOT_FOUND)
        run = self._snapshot_run(run_record)
        self._validate_run(run, data)

        candidate_record = _translated_call(
            lambda: self.staging_repository.get_candidate_for_promotion(
                data.project_id, data.candidate_id
            )
        )
        if candidate_record is None:
            raise AgentCompanyApplyNotFoundError(_NOT_FOUND)
        before = self._snapshot_candidate(candidate_record)
        before_status = self._validate_candidate(before, data)

        reviewed = False
        if before_status is CompanyDiscoveryCandidateStatus.DISCOVERED:
            review_result = self._review(data)
            self._validate_review_result(review_result, data)
            reviewed = True
        elif before_status is CompanyDiscoveryCandidateStatus.REJECTED:
            raise AgentCompanyApplyNotEligibleError(_CANDIDATE_INELIGIBLE)

        promotion_result = self._promote(data)
        promotion = self._validate_promotion_result(promotion_result, data, before_status)

        final_record = _translated_call(
            lambda: self.staging_repository.get_candidate_for_promotion(
                data.project_id, data.candidate_id
            )
        )
        if final_record is None:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        final_candidate = self._snapshot_candidate(final_record)
        company_id = self._validate_final_candidate(final_candidate, data, promotion.company_id)

        company_record = _translated_call(
            lambda: self.company_repository.get_for_project(data.project_id, company_id)
        )
        if company_record is None:
            raise AgentCompanyApplyConsistencyError(_COMPANY_INVALID)
        company = self._snapshot_company(company_record)
        self._validate_company(company, data.project_id, company_id)

        promoted_now = before_status is not CompanyDiscoveryCandidateStatus.PROMOTED
        result_failed = False
        result: AgentCompanyApplyResult | None = None
        try:
            result = AgentCompanyApplyResult(
                project_id=data.project_id,
                discovery_run_id=data.discovery_run_id,
                candidate_id=data.candidate_id,
                company_id=company_id,
                candidate_status_before=before_status,
                candidate_status_after=CompanyDiscoveryCandidateStatus.PROMOTED,
                company_created=promotion.created_company,
                company_reused=not promotion.created_company,
                candidate_reviewed=reviewed,
                candidate_promoted=promoted_now,
                crm_mutated=promoted_now,
                network_call_count=0,
                contact_mutation_count=0,
                lead_mutation_count=0,
                task_mutation_count=0,
                human_confirmation_required=True,
                human_confirmation_received=True,
            )
        except (TypeError, ValueError, ValidationError):
            result_failed = True
        if result_failed or result is None:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return result

    @staticmethod
    def _validate_input(value: AgentCompanyApplyInput) -> AgentCompanyApplyInput:
        if type(value) is not AgentCompanyApplyInput:
            raise AgentCompanyApplyInvalidDataError(_INVALID)
        confirmation_missing = False
        try:
            confirmed = value.confirmed
        except AttributeError:
            confirmation_missing = True
            confirmed = None
        if confirmation_missing or type(confirmed) is not bool or confirmed is not True:
            raise AgentCompanyApplyConfirmationRequiredError(_CONFIRMATION)
        invalid = False
        validated: AgentCompanyApplyInput | None = None
        try:
            validated = AgentCompanyApplyInput(
                project_id=value.project_id,
                discovery_run_id=value.discovery_run_id,
                candidate_id=value.candidate_id,
                confirmed=confirmed,
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            invalid = True
        if invalid or validated is None:
            raise AgentCompanyApplyInvalidDataError(_INVALID)
        return validated

    def _acquire_project(self, project_id: int) -> None:
        missing = False
        conflict = False
        failed = False
        try:
            self.company_repository.acquire_promotion_scope(project_id)
        except IntegrityError:
            conflict = True
        except (TypeError, ValueError):
            missing = True
        except Exception:
            failed = True
        if conflict:
            raise AgentCompanyApplyConflictError(_CONFLICT)
        if missing:
            raise AgentCompanyApplyNotFoundError(_NOT_FOUND)
        if failed:
            raise AgentCompanyApplyPersistenceError(_PERSISTENCE)

    @staticmethod
    def _snapshot_run(record: _RunRecord) -> _RunSnapshot:
        failed = False
        try:
            snapshot = _RunSnapshot(record.id, record.project_id, record.run_status)
        except AttributeError:
            failed = True
        if failed:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return snapshot

    @classmethod
    def _validate_run(
        cls, run: _RunSnapshot, data: AgentCompanyApplyInput
    ) -> CompanyDiscoveryRunStatus:
        if type(run.id) is not int or run.id <= 0:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        if type(run.project_id) is not int or run.project_id <= 0:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        if run.id != data.discovery_run_id or run.project_id != data.project_id:
            raise AgentCompanyApplyNotFoundError(_NOT_FOUND)
        status = cls._run_status(run.run_status)
        if status not in (CompanyDiscoveryRunStatus.SUCCEEDED, CompanyDiscoveryRunStatus.PARTIAL):
            raise AgentCompanyApplyNotEligibleError(_RUN_INELIGIBLE)
        return status

    @staticmethod
    def _run_status(value: object) -> CompanyDiscoveryRunStatus:
        if type(value) is CompanyDiscoveryRunStatus:
            return value
        if type(value) is str:
            try:
                return CompanyDiscoveryRunStatus(value)
            except ValueError:
                pass
        raise AgentCompanyApplyConsistencyError(_INCONSISTENT)

    @staticmethod
    def _snapshot_candidate(record: _CandidateRecord) -> _CandidateSnapshot:
        failed = False
        try:
            snapshot = _CandidateSnapshot(
                record.id,
                record.project_id,
                record.first_seen_run_id,
                record.last_seen_run_id,
                record.name,
                record.website,
                record.country_code,
                record.candidate_status,
                record.promoted_company_id,
            )
        except AttributeError:
            failed = True
        if failed:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return snapshot

    @classmethod
    def _validate_candidate(
        cls, candidate: _CandidateSnapshot, data: AgentCompanyApplyInput
    ) -> CompanyDiscoveryCandidateStatus:
        for identifier in (
            candidate.id,
            candidate.project_id,
            candidate.first_seen_run_id,
            candidate.last_seen_run_id,
        ):
            if type(identifier) is not int or identifier <= 0:
                raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        if candidate.id != data.candidate_id or candidate.project_id != data.project_id:
            raise AgentCompanyApplyNotFoundError(_NOT_FOUND)
        if candidate.last_seen_run_id != data.discovery_run_id:
            raise AgentCompanyApplyStaleHandoffError(_STALE)
        status = cls._candidate_status(candidate.candidate_status)
        linked = candidate.promoted_company_id
        if status is CompanyDiscoveryCandidateStatus.PROMOTED:
            if type(linked) is not int or linked <= 0:
                raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        elif linked is not None:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return status

    @staticmethod
    def _candidate_status(value: object) -> CompanyDiscoveryCandidateStatus:
        if type(value) is CompanyDiscoveryCandidateStatus:
            return value
        if type(value) is str:
            try:
                return CompanyDiscoveryCandidateStatus(value)
            except ValueError:
                pass
        raise AgentCompanyApplyConsistencyError(_INCONSISTENT)

    def _review(self, data: AgentCompanyApplyInput) -> CompanyDiscoveryCandidateReviewResult:
        conflict = False
        failed = False
        result: CompanyDiscoveryCandidateReviewResult | None = None
        try:
            result = self.review_service.mark_reviewed(data.project_id, data.candidate_id)
        except IntegrityError:
            conflict = True
        except (
            CompanyDiscoveryCandidateReviewNotFoundError,
            CompanyDiscoveryCandidateTransitionError,
        ):
            failed = True
        except Exception:
            failed = True
        if conflict:
            raise AgentCompanyApplyConflictError(_CONFLICT)
        if failed or result is None:
            raise AgentCompanyApplyPersistenceError(_PERSISTENCE)
        return result

    @staticmethod
    def _validate_review_result(
        raw: CompanyDiscoveryCandidateReviewResult, data: AgentCompanyApplyInput
    ) -> CompanyDiscoveryCandidateReviewResult:
        invalid = False
        result: CompanyDiscoveryCandidateReviewResult | None = None
        try:
            if type(raw) is not CompanyDiscoveryCandidateReviewResult:
                raise TypeError
            candidate = raw.candidate
            if type(candidate) is not CompanyDiscoveryCandidateRead:
                raise TypeError
            candidate_copy = CompanyDiscoveryCandidateRead(
                id=candidate.id,
                project_id=candidate.project_id,
                first_seen_run_id=candidate.first_seen_run_id,
                last_seen_run_id=candidate.last_seen_run_id,
                provider=candidate.provider,
                name=candidate.name,
                normalized_name=candidate.normalized_name,
                website=candidate.website,
                website_identity=candidate.website_identity,
                country_code=candidate.country_code,
                identity_key=candidate.identity_key,
                best_position=candidate.best_position,
                candidate_status=candidate.candidate_status,
                promoted_company_id=candidate.promoted_company_id,
                created_at=candidate.created_at,
                updated_at=candidate.updated_at,
            )
            result = CompanyDiscoveryCandidateReviewResult(
                candidate=candidate_copy,
                previous_status=raw.previous_status,
                current_status=raw.current_status,
                changed=raw.changed,
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            invalid = True
        if invalid or result is None:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        if (
            result.candidate.id != data.candidate_id
            or result.candidate.project_id != data.project_id
            or result.previous_status is not CompanyDiscoveryCandidateStatus.DISCOVERED
            or result.current_status is not CompanyDiscoveryCandidateStatus.REVIEWED
            or result.candidate.candidate_status is not CompanyDiscoveryCandidateStatus.REVIEWED
            or result.changed is not True
        ):
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return result

    def _promote(self, data: AgentCompanyApplyInput) -> CompanyDiscoveryCandidatePromotionResult:
        conflict = False
        inconsistent = False
        failed = False
        result: CompanyDiscoveryCandidatePromotionResult | None = None
        try:
            result = self.promotion_service.promote(data.project_id, data.candidate_id)
        except IntegrityError:
            conflict = True
        except (
            CompanyDiscoveryCandidatePromotionNotFoundError,
            CompanyDiscoveryCandidateNotEligibleError,
            CompanyDiscoveryCandidatePromotionInvalidDataError,
            CompanyDiscoveryCandidatePromotionConsistencyError,
        ):
            inconsistent = True
        except Exception:
            failed = True
        if conflict:
            raise AgentCompanyApplyConflictError(_CONFLICT)
        if inconsistent:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        if failed or result is None:
            raise AgentCompanyApplyPersistenceError(_PERSISTENCE)
        return result

    @staticmethod
    def _validate_promotion_result(
        raw: CompanyDiscoveryCandidatePromotionResult,
        data: AgentCompanyApplyInput,
        before: CompanyDiscoveryCandidateStatus,
    ) -> CompanyDiscoveryCandidatePromotionResult:
        invalid = False
        result: CompanyDiscoveryCandidatePromotionResult | None = None
        try:
            if type(raw) is not CompanyDiscoveryCandidatePromotionResult:
                raise TypeError
            result = CompanyDiscoveryCandidatePromotionResult(
                candidate_id=raw.candidate_id,
                project_id=raw.project_id,
                company_id=raw.company_id,
                previous_status=raw.previous_status,
                current_status=raw.current_status,
                created_company=raw.created_company,
                changed=raw.changed,
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            invalid = True
        if invalid or result is None:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        expected_previous = (
            CompanyDiscoveryCandidateStatus.PROMOTED
            if before is CompanyDiscoveryCandidateStatus.PROMOTED
            else CompanyDiscoveryCandidateStatus.REVIEWED
        )
        if (
            result.candidate_id != data.candidate_id
            or result.project_id != data.project_id
            or result.previous_status is not expected_previous
            or result.current_status is not CompanyDiscoveryCandidateStatus.PROMOTED
            or result.changed is (before is CompanyDiscoveryCandidateStatus.PROMOTED)
        ):
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return result

    @classmethod
    def _validate_final_candidate(
        cls, candidate: _CandidateSnapshot, data: AgentCompanyApplyInput, company_id: int
    ) -> int:
        status = cls._validate_candidate(candidate, data)
        if status is not CompanyDiscoveryCandidateStatus.PROMOTED:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        if candidate.promoted_company_id != company_id:
            raise AgentCompanyApplyConsistencyError(_INCONSISTENT)
        return company_id

    @staticmethod
    def _snapshot_company(record: _CompanyRecord) -> _CompanySnapshot:
        failed = False
        try:
            snapshot = _CompanySnapshot(record.id, record.project_id)
        except AttributeError:
            failed = True
        if failed:
            raise AgentCompanyApplyConsistencyError(_COMPANY_INVALID)
        return snapshot

    @staticmethod
    def _validate_company(company: _CompanySnapshot, project_id: int, company_id: int) -> None:
        if (
            type(company.id) is not int
            or company.id <= 0
            or type(company.project_id) is not int
            or company.project_id <= 0
            or company.id != company_id
            or company.project_id != project_id
        ):
            raise AgentCompanyApplyConsistencyError(_COMPANY_INVALID)


__all__ = [
    "AgentCompanyApplyConflictError",
    "AgentCompanyApplyConfirmationRequiredError",
    "AgentCompanyApplyConsistencyError",
    "AgentCompanyApplyError",
    "AgentCompanyApplyInternalError",
    "AgentCompanyApplyInvalidDataError",
    "AgentCompanyApplyNotEligibleError",
    "AgentCompanyApplyNotFoundError",
    "AgentCompanyApplyPersistenceError",
    "AgentCompanyApplyService",
    "AgentCompanyApplyStaleHandoffError",
]

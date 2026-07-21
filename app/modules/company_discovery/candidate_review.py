from collections.abc import Sequence
from typing import Protocol

from app.modules.company_discovery.candidate_review_schemas import (
    CompanyDiscoveryCandidateReviewResult,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateRead


class CompanyDiscoveryCandidateReviewNotFoundError(ValueError):
    pass


class CompanyDiscoveryCandidateTransitionError(ValueError):
    pass


class CandidateReviewCandidateRecord(Protocol):
    id: int
    candidate_status: CompanyDiscoveryCandidateStatus


class CandidateReviewRepository(Protocol):
    def get_candidate_for_project(
        self,
        project_id: int,
        candidate_id: int,
    ) -> CandidateReviewCandidateRecord | None: ...

    def set_candidate_status(
        self,
        project_id: int,
        candidate_id: int,
        candidate_status: CompanyDiscoveryCandidateStatus,
    ) -> CandidateReviewCandidateRecord: ...

    def list_candidates_for_project(
        self,
        project_id: int,
        limit: int,
        offset: int = 0,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> Sequence[CandidateReviewCandidateRecord]: ...


class CompanyDiscoveryCandidateReviewService:
    def __init__(self, repository: CandidateReviewRepository) -> None:
        self.repository = repository

    def get_candidate(
        self,
        project_id: int,
        candidate_id: int,
    ) -> CompanyDiscoveryCandidateRead:
        self._validate_positive_id(project_id, "Project")
        self._validate_positive_id(candidate_id, "Candidate")

        candidate = self.repository.get_candidate_for_project(project_id, candidate_id)
        if candidate is None:
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")

        return CompanyDiscoveryCandidateRead.model_validate(candidate)

    def list_candidates(
        self,
        project_id: int,
        limit: int,
        offset: int = 0,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> list[CompanyDiscoveryCandidateRead]:
        self._validate_positive_id(project_id, "Project")
        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("Limit must be a positive integer.")
        if isinstance(offset, bool) or offset < 0:
            raise ValueError("Offset must not be negative.")
        if candidate_status is not None and not isinstance(
            candidate_status, CompanyDiscoveryCandidateStatus
        ):
            raise ValueError("Invalid candidate status filter.")

        candidates = self.repository.list_candidates_for_project(
            project_id=project_id,
            limit=limit,
            offset=offset,
            candidate_status=candidate_status,
        )
        return [CompanyDiscoveryCandidateRead.model_validate(candidate) for candidate in candidates]

    def mark_reviewed(
        self,
        project_id: int,
        candidate_id: int,
    ) -> CompanyDiscoveryCandidateReviewResult:
        return self._apply_transition(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_status=CompanyDiscoveryCandidateStatus.REVIEWED,
        )

    def reject(
        self,
        project_id: int,
        candidate_id: int,
    ) -> CompanyDiscoveryCandidateReviewResult:
        return self._apply_transition(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_status=CompanyDiscoveryCandidateStatus.REJECTED,
        )

    def _apply_transition(
        self,
        *,
        project_id: int,
        candidate_id: int,
        candidate_status: CompanyDiscoveryCandidateStatus,
    ) -> CompanyDiscoveryCandidateReviewResult:
        self._validate_positive_id(project_id, "Project")
        self._validate_positive_id(candidate_id, "Candidate")

        if candidate_status not in (
            CompanyDiscoveryCandidateStatus.REVIEWED,
            CompanyDiscoveryCandidateStatus.REJECTED,
        ):
            raise CompanyDiscoveryCandidateTransitionError(
                "Candidate status transition is not allowed."
            )

        candidate = self.repository.get_candidate_for_project(project_id, candidate_id)
        if candidate is None:
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")

        previous_status = candidate.candidate_status
        if not self._is_allowed_transition(previous_status, candidate_status):
            raise CompanyDiscoveryCandidateTransitionError(
                "Candidate status transition is not allowed."
            )

        if previous_status != candidate_status:
            candidate = self.repository.set_candidate_status(
                project_id=project_id,
                candidate_id=candidate_id,
                candidate_status=candidate_status,
            )
            changed = True
        else:
            changed = False

        return CompanyDiscoveryCandidateReviewResult(
            candidate=CompanyDiscoveryCandidateRead.model_validate(candidate),
            previous_status=previous_status,
            current_status=candidate_status,
            changed=changed,
        )

    @staticmethod
    def _is_allowed_transition(
        current_status: CompanyDiscoveryCandidateStatus,
        next_status: CompanyDiscoveryCandidateStatus,
    ) -> bool:
        allowed: dict[
            CompanyDiscoveryCandidateStatus, Sequence[CompanyDiscoveryCandidateStatus]
        ] = {
            CompanyDiscoveryCandidateStatus.DISCOVERED: (
                CompanyDiscoveryCandidateStatus.REVIEWED,
                CompanyDiscoveryCandidateStatus.REJECTED,
            ),
            CompanyDiscoveryCandidateStatus.REVIEWED: (
                CompanyDiscoveryCandidateStatus.REVIEWED,
                CompanyDiscoveryCandidateStatus.REJECTED,
            ),
            CompanyDiscoveryCandidateStatus.REJECTED: (CompanyDiscoveryCandidateStatus.REJECTED,),
            CompanyDiscoveryCandidateStatus.PROMOTED: (),
        }
        if current_status not in allowed:
            return False
        return next_status in allowed[current_status]

    @staticmethod
    def _validate_positive_id(value: int, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} ID must be a positive integer.")

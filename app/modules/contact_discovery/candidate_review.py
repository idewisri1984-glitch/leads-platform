from collections.abc import Sequence
from typing import Protocol

from app.modules.contact_discovery.candidate_review_schemas import (
    ContactDiscoveryCandidateReviewResult,
)
from app.modules.contact_discovery.models import ContactDiscoveryCandidateStatus
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateRead


class ContactDiscoveryCandidateReviewNotFoundError(ValueError):
    pass


class ContactDiscoveryCandidateTransitionError(ValueError):
    pass


class CandidateReviewRecord(Protocol):
    discovery_status: ContactDiscoveryCandidateStatus


class CandidateReviewRepository(Protocol):
    def get_candidate_for_company(
        self, company_id: int, candidate_id: int
    ) -> CandidateReviewRecord | None: ...

    def list_candidates_for_company(
        self,
        company_id: int,
        limit: int = 100,
        offset: int = 0,
        candidate_status: ContactDiscoveryCandidateStatus | None = None,
    ) -> Sequence[CandidateReviewRecord]: ...

    def set_candidate_status(
        self,
        company_id: int,
        candidate_id: int,
        candidate_status: ContactDiscoveryCandidateStatus,
    ) -> CandidateReviewRecord: ...


class ContactDiscoveryCandidateReviewService:
    def __init__(self, repository: CandidateReviewRepository) -> None:
        self.repository = repository

    def get_candidate(self, company_id: int, candidate_id: int) -> ContactDiscoveryCandidateRead:
        self._validate_positive_id(company_id, "Company")
        self._validate_positive_id(candidate_id, "Candidate")
        candidate = self.repository.get_candidate_for_company(company_id, candidate_id)
        if candidate is None:
            raise ContactDiscoveryCandidateReviewNotFoundError("Candidate was not found.")
        return ContactDiscoveryCandidateRead.model_validate(candidate)

    def list_candidates(
        self,
        company_id: int,
        limit: int,
        offset: int = 0,
        candidate_status: ContactDiscoveryCandidateStatus | None = None,
    ) -> list[ContactDiscoveryCandidateRead]:
        self._validate_positive_id(company_id, "Company")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Limit must be an integer from 1 through 100.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Offset must be a non-negative integer.")
        if candidate_status is not None and not isinstance(
            candidate_status, ContactDiscoveryCandidateStatus
        ):
            raise ValueError("Invalid candidate status filter.")
        candidates = self.repository.list_candidates_for_company(
            company_id,
            limit=limit,
            offset=offset,
            candidate_status=candidate_status,
        )
        return [ContactDiscoveryCandidateRead.model_validate(candidate) for candidate in candidates]

    def mark_reviewed(
        self, company_id: int, candidate_id: int
    ) -> ContactDiscoveryCandidateReviewResult:
        return self._apply_transition(
            company_id, candidate_id, ContactDiscoveryCandidateStatus.REVIEWED
        )

    def reject(self, company_id: int, candidate_id: int) -> ContactDiscoveryCandidateReviewResult:
        return self._apply_transition(
            company_id, candidate_id, ContactDiscoveryCandidateStatus.REJECTED
        )

    def _apply_transition(
        self,
        company_id: int,
        candidate_id: int,
        target: ContactDiscoveryCandidateStatus,
    ) -> ContactDiscoveryCandidateReviewResult:
        self._validate_positive_id(company_id, "Company")
        self._validate_positive_id(candidate_id, "Candidate")
        candidate = self.repository.get_candidate_for_company(company_id, candidate_id)
        if candidate is None:
            raise ContactDiscoveryCandidateReviewNotFoundError("Candidate was not found.")
        previous = candidate.discovery_status
        allowed = {
            ContactDiscoveryCandidateStatus.DISCOVERED: (
                ContactDiscoveryCandidateStatus.REVIEWED,
                ContactDiscoveryCandidateStatus.REJECTED,
            ),
            ContactDiscoveryCandidateStatus.REVIEWED: (
                ContactDiscoveryCandidateStatus.REVIEWED,
                ContactDiscoveryCandidateStatus.REJECTED,
            ),
            ContactDiscoveryCandidateStatus.REJECTED: (ContactDiscoveryCandidateStatus.REJECTED,),
            ContactDiscoveryCandidateStatus.PROMOTED: (),
        }
        if target not in allowed.get(previous, ()):
            raise ContactDiscoveryCandidateTransitionError(
                "Candidate status transition is not allowed."
            )
        changed = previous != target
        if changed:
            try:
                candidate = self.repository.set_candidate_status(company_id, candidate_id, target)
            except ValueError:
                raise ContactDiscoveryCandidateReviewNotFoundError(
                    "Candidate was not found."
                ) from None
        return ContactDiscoveryCandidateReviewResult(
            candidate=ContactDiscoveryCandidateRead.model_validate(candidate),
            previous_status=previous,
            current_status=target,
            changed=changed,
        )

    @staticmethod
    def _validate_positive_id(value: int, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} ID must be a positive integer.")

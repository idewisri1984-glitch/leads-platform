from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest

from app.modules.company_discovery import (
    CompanyDiscoveryCandidateReviewNotFoundError,
    CompanyDiscoveryCandidateReviewService,
    CompanyDiscoveryCandidateTransitionError,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus


@dataclass
class FakeCandidate:
    id: int
    project_id: int
    first_seen_run_id: int = 1
    last_seen_run_id: int = 2
    provider: str = "serpapi"
    name: str | None = "Acme"
    normalized_name: str | None = "acme"
    website: str | None = "https://acme.example"
    website_identity: str | None = "acme.example"
    country_code: str | None = "US"
    identity_key: str = "website:acme.example"
    best_position: int | None = 5
    candidate_status: CompanyDiscoveryCandidateStatus = CompanyDiscoveryCandidateStatus.DISCOVERED
    promoted_company_id: int | None = None
    created_at: datetime = datetime.now(UTC)
    updated_at: datetime = datetime.now(UTC)


class FakeRepository:
    def __init__(self, candidate: FakeCandidate | None = None) -> None:
        self.candidate = candidate
        self.get_calls: list[tuple[int, int]] = []
        self.set_calls: list[tuple[int, int, CompanyDiscoveryCandidateStatus]] = []
        self.list_calls: list[tuple[int, int, CompanyDiscoveryCandidateStatus | None]] = []
        self.fail_set_status: Exception | BaseException | None = None
        self.list_candidates_return: list[FakeCandidate] = []

    def get_candidate_for_project(self, project_id: int, candidate_id: int) -> FakeCandidate | None:
        self.get_calls.append((project_id, candidate_id))
        if self.candidate is None:
            return None
        if self.candidate.id != candidate_id or self.candidate.project_id != project_id:
            return None
        return self.candidate

    def set_candidate_status(
        self,
        project_id: int,
        candidate_id: int,
        candidate_status: CompanyDiscoveryCandidateStatus,
    ) -> FakeCandidate:
        self.set_calls.append((project_id, candidate_id, candidate_status))
        if self.fail_set_status is not None:
            raise self.fail_set_status
        if self.candidate is None:
            raise AssertionError("candidate is missing")
        self.candidate.candidate_status = candidate_status
        return self.candidate

    def list_candidates_for_project(
        self,
        project_id: int,
        limit: int,
        offset: int = 0,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> list[FakeCandidate]:
        self.list_calls.append((project_id, limit, candidate_status))
        return list(self.list_candidates_return)[offset : offset + limit]


def create_service(
    candidate: FakeCandidate | None = None,
) -> tuple[CompanyDiscoveryCandidateReviewService, FakeRepository]:
    repository = FakeRepository(candidate=candidate)
    return CompanyDiscoveryCandidateReviewService(repository), repository


def test_mark_reviewed_discovers_candidate() -> None:
    candidate = FakeCandidate(id=11, project_id=7)
    service, repository = create_service(candidate)

    result = service.mark_reviewed(project_id=7, candidate_id=11)

    assert result.previous_status == CompanyDiscoveryCandidateStatus.DISCOVERED
    assert result.current_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert result.changed is True
    assert result.candidate.candidate_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert repository.set_calls == [(7, 11, CompanyDiscoveryCandidateStatus.REVIEWED)]


def test_reject_discovered_candidate() -> None:
    candidate = FakeCandidate(id=11, project_id=7)
    service, repository = create_service(candidate)

    result = service.reject(project_id=7, candidate_id=11)

    assert result.previous_status == CompanyDiscoveryCandidateStatus.DISCOVERED
    assert result.current_status == CompanyDiscoveryCandidateStatus.REJECTED
    assert result.changed is True
    assert repository.set_calls == [(7, 11, CompanyDiscoveryCandidateStatus.REJECTED)]


def test_reviewed_candidate_can_be_rejected() -> None:
    candidate = FakeCandidate(
        id=11,
        project_id=7,
        candidate_status=CompanyDiscoveryCandidateStatus.REVIEWED,
    )
    service, repository = create_service(candidate)

    result = service.reject(project_id=7, candidate_id=11)

    assert result.previous_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert result.current_status == CompanyDiscoveryCandidateStatus.REJECTED
    assert result.changed is True
    assert repository.set_calls == [(7, 11, CompanyDiscoveryCandidateStatus.REJECTED)]


def test_review_mark_is_idempotent() -> None:
    candidate = FakeCandidate(
        id=11,
        project_id=7,
        candidate_status=CompanyDiscoveryCandidateStatus.REVIEWED,
    )
    service, repository = create_service(candidate)

    result = service.mark_reviewed(project_id=7, candidate_id=11)

    assert result.previous_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert result.current_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert result.changed is False
    assert repository.set_calls == []


def test_reject_is_idempotent() -> None:
    candidate = FakeCandidate(
        id=11,
        project_id=7,
        candidate_status=CompanyDiscoveryCandidateStatus.REJECTED,
    )
    service, repository = create_service(candidate)

    result = service.reject(project_id=7, candidate_id=11)

    assert result.previous_status == CompanyDiscoveryCandidateStatus.REJECTED
    assert result.current_status == CompanyDiscoveryCandidateStatus.REJECTED
    assert result.changed is False
    assert repository.set_calls == []


@pytest.mark.parametrize(
    ("candidate_status", "action"),
    [
        (CompanyDiscoveryCandidateStatus.PROMOTED, "mark_reviewed"),
        (CompanyDiscoveryCandidateStatus.PROMOTED, "reject"),
    ],
)
def test_forbidden_transition_is_rejected(
    candidate_status: CompanyDiscoveryCandidateStatus,
    action: str,
) -> None:
    candidate = FakeCandidate(id=11, project_id=7, candidate_status=candidate_status)
    service, _ = create_service(candidate)
    transition = service.mark_reviewed if action == "mark_reviewed" else service.reject

    with pytest.raises(CompanyDiscoveryCandidateTransitionError):
        transition(project_id=7, candidate_id=11)


@pytest.mark.parametrize(
    ("from_status", "target_status"),
    [
        (CompanyDiscoveryCandidateStatus.REVIEWED, CompanyDiscoveryCandidateStatus.DISCOVERED),
        (CompanyDiscoveryCandidateStatus.REJECTED, CompanyDiscoveryCandidateStatus.DISCOVERED),
        (CompanyDiscoveryCandidateStatus.REJECTED, CompanyDiscoveryCandidateStatus.REVIEWED),
    ],
)
def test_forbidden_status_transition_is_rejected(
    from_status: CompanyDiscoveryCandidateStatus,
    target_status: CompanyDiscoveryCandidateStatus,
) -> None:
    service, _ = create_service(FakeCandidate(id=11, project_id=7, candidate_status=from_status))
    with pytest.raises(CompanyDiscoveryCandidateTransitionError):
        service._apply_transition(
            project_id=7,
            candidate_id=11,
            candidate_status=target_status,
        )


def test_direct_promotion_to_reviewed_is_unavailable() -> None:
    candidate = FakeCandidate(id=11, project_id=7)
    service, _ = create_service(candidate)

    with pytest.raises(CompanyDiscoveryCandidateTransitionError):
        service._apply_transition(
            project_id=7,
            candidate_id=11,
            candidate_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        )


def test_missing_candidate_is_not_found() -> None:
    service, repository = create_service(None)

    with pytest.raises(CompanyDiscoveryCandidateReviewNotFoundError):
        service.mark_reviewed(project_id=7, candidate_id=11)

    assert repository.get_calls == [(7, 11)]
    assert repository.set_calls == []


def test_cross_project_candidate_is_not_found() -> None:
    candidate = FakeCandidate(id=11, project_id=9)
    service, _ = create_service(candidate)

    with pytest.raises(CompanyDiscoveryCandidateReviewNotFoundError):
        service.mark_reviewed(project_id=7, candidate_id=11)


def test_invalid_ids_are_rejected() -> None:
    service, _ = create_service(FakeCandidate(id=11, project_id=7))

    with pytest.raises(ValueError):
        service.mark_reviewed(project_id=0, candidate_id=11)
    with pytest.raises(ValueError):
        service.mark_reviewed(project_id=7, candidate_id=0)
    with pytest.raises(ValueError):
        service.mark_reviewed(project_id=cast(int, True), candidate_id=11)


def test_transition_error_does_not_write_without_permission() -> None:
    candidate = FakeCandidate(
        id=11,
        project_id=7,
        candidate_status=CompanyDiscoveryCandidateStatus.REJECTED,
    )
    service, repository = create_service(candidate)

    with pytest.raises(CompanyDiscoveryCandidateTransitionError):
        service.mark_reviewed(project_id=7, candidate_id=11)

    assert repository.set_calls == []


def test_persistence_exception_is_propagated() -> None:
    candidate = FakeCandidate(id=11, project_id=7)
    service, repository = create_service(candidate)
    repository.fail_set_status = RuntimeError("commit unavailable")

    with pytest.raises(RuntimeError):
        service.mark_reviewed(project_id=7, candidate_id=11)


class CustomBaseException(BaseException):
    pass


def test_base_exception_is_propagated() -> None:
    candidate = FakeCandidate(id=11, project_id=7)
    service, repository = create_service(candidate)
    repository.fail_set_status = CustomBaseException("critical")

    with pytest.raises(CustomBaseException):
        service.mark_reviewed(project_id=7, candidate_id=11)


def test_list_candidates_is_project_scoped_and_paginated() -> None:
    candidate = FakeCandidate(id=11, project_id=7)
    service, repository = create_service(candidate)
    repository.list_candidates_return = [candidate]

    result = service.list_candidates(
        project_id=7,
        limit=10,
        offset=0,
        candidate_status=CompanyDiscoveryCandidateStatus.DISCOVERED,
    )

    assert len(result) == 1
    assert repository.list_calls == [(7, 10, CompanyDiscoveryCandidateStatus.DISCOVERED)]

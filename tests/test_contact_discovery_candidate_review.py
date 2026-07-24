from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.modules.contact_discovery import (
    ContactDiscoveryCandidateReviewNotFoundError,
    ContactDiscoveryCandidateReviewResult,
    ContactDiscoveryCandidateReviewService,
    ContactDiscoveryCandidateStatus,
    ContactDiscoveryCandidateTransitionError,
    ContactDiscoverySourceType,
)


def record(status: ContactDiscoveryCandidateStatus) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=7,
        company_id=3,
        name="Person",
        title="Director",
        email="person@example.com",
        normalized_email="person@example.com",
        phone="+1 555 0100",
        source_url="https://example.com/team",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
        confidence=80,
        discovery_status=status,
        deduplication_key="email:person@example.com",
        notes=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


class FakeRepository:
    def __init__(self, status: ContactDiscoveryCandidateStatus) -> None:
        self.candidate = record(status)
        self.mutations: list[ContactDiscoveryCandidateStatus] = []

    def get_candidate_for_company(self, company_id: int, candidate_id: int) -> object | None:
        return self.candidate if (company_id, candidate_id) == (3, 7) else None

    def list_candidates_for_company(
        self,
        company_id: int,
        limit: int = 100,
        offset: int = 0,
        candidate_status: ContactDiscoveryCandidateStatus | None = None,
    ) -> list[object]:
        if company_id != 3 or offset:
            return []
        if candidate_status is not None and candidate_status != self.candidate.discovery_status:
            return []
        return [self.candidate][:limit]

    def set_candidate_status(
        self,
        company_id: int,
        candidate_id: int,
        candidate_status: ContactDiscoveryCandidateStatus,
    ) -> object:
        assert (company_id, candidate_id) == (3, 7)
        self.mutations.append(candidate_status)
        self.candidate.discovery_status = candidate_status
        return self.candidate


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "1"])
def test_service_rejects_invalid_ids(value: object) -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    with pytest.raises(ValueError):
        service.get_candidate(value, 7)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        service.get_candidate(3, value)  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", [1, 100])
def test_list_accepts_boundary_limits(limit: int) -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    assert len(service.list_candidates(3, limit)) == 1


@pytest.mark.parametrize("limit", [0, 101, -1, True, False, 1.5, "1"])
def test_list_rejects_invalid_limits(limit: object) -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    with pytest.raises(ValueError):
        service.list_candidates(3, limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("offset", [-1, True, False, 1.5, "0"])
def test_list_rejects_invalid_offsets(offset: object) -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    with pytest.raises(ValueError):
        service.list_candidates(3, 10, offset)  # type: ignore[arg-type]


def test_list_accepts_zero_offset_and_enum_filter() -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    assert service.list_candidates(3, 10, 0, ContactDiscoveryCandidateStatus.DISCOVERED)
    with pytest.raises(ValueError, match="Invalid candidate status filter"):
        service.list_candidates(3, 10, candidate_status="DISCOVERED")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start", "operation", "end", "changed", "mutation_count"),
    [
        ("DISCOVERED", "review", "REVIEWED", True, 1),
        ("DISCOVERED", "reject", "REJECTED", True, 1),
        ("REVIEWED", "review", "REVIEWED", False, 0),
        ("REVIEWED", "reject", "REJECTED", True, 1),
        ("REJECTED", "reject", "REJECTED", False, 0),
    ],
)
def test_allowed_transition_matrix(
    start: str, operation: str, end: str, changed: bool, mutation_count: int
) -> None:
    repository = FakeRepository(ContactDiscoveryCandidateStatus(start))
    service = ContactDiscoveryCandidateReviewService(repository)
    result = service.mark_reviewed(3, 7) if operation == "review" else service.reject(3, 7)
    assert result.current_status == ContactDiscoveryCandidateStatus(end)
    assert result.changed is changed
    assert len(repository.mutations) == mutation_count


@pytest.mark.parametrize(
    ("start", "operation"),
    [("REJECTED", "review"), ("PROMOTED", "review"), ("PROMOTED", "reject")],
)
def test_forbidden_transition_matrix(start: str, operation: str) -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus(start))
    )
    with pytest.raises(
        ContactDiscoveryCandidateTransitionError,
        match=r"^Candidate status transition is not allowed\.$",
    ):
        if operation == "review":
            service.mark_reviewed(3, 7)
        else:
            service.reject(3, 7)


def test_cross_company_candidate_is_sanitized_not_found() -> None:
    service = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    with pytest.raises(
        ContactDiscoveryCandidateReviewNotFoundError,
        match=r"^Candidate was not found\.$",
    ):
        service.get_candidate(4, 7)


def test_result_schema_is_frozen_and_enforces_changed_invariant() -> None:
    candidate = ContactDiscoveryCandidateReviewService(
        FakeRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    ).get_candidate(3, 7)
    with pytest.raises(ValidationError):
        ContactDiscoveryCandidateReviewResult(
            candidate=candidate,
            previous_status=ContactDiscoveryCandidateStatus.DISCOVERED,
            current_status=ContactDiscoveryCandidateStatus.REVIEWED,
            changed=False,
        )
    result = ContactDiscoveryCandidateReviewResult(
        candidate=candidate,
        previous_status=ContactDiscoveryCandidateStatus.DISCOVERED,
        current_status=ContactDiscoveryCandidateStatus.REVIEWED,
        changed=True,
    )
    with pytest.raises(ValidationError):
        result.changed = False  # type: ignore[misc]


def test_base_exception_from_repository_is_not_swallowed() -> None:
    class InterruptingRepository(FakeRepository):
        def get_candidate_for_company(self, company_id: int, candidate_id: int) -> object:
            raise KeyboardInterrupt

    service = ContactDiscoveryCandidateReviewService(
        InterruptingRepository(ContactDiscoveryCandidateStatus.DISCOVERED)
    )
    with pytest.raises(KeyboardInterrupt):
        service.mark_reviewed(3, 7)

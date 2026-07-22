from dataclasses import dataclass
from inspect import getsource
from typing import cast

import pytest

from app.modules.company_discovery import (
    CompanyDiscoveryCandidateNotEligibleError,
    CompanyDiscoveryCandidatePromotionConsistencyError,
    CompanyDiscoveryCandidatePromotionInvalidDataError,
    CompanyDiscoveryCandidatePromotionNotFoundError,
    CompanyDiscoveryCandidatePromotionService,
)
from app.modules.company_discovery.candidate_promotion import (
    CandidatePromotionCompanyRepository,
    CandidatePromotionStagingRepository,
)
from app.modules.company_discovery.candidate_promotion_schemas import (
    CompanyDiscoveryCandidatePromotionResult,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus


@dataclass
class FakeCandidate:
    id: int = 34
    project_id: int = 12
    name: str | None = "Acme"
    website: str | None = "https://www.example.com/about"
    country_code: str | None = "US"
    candidate_status: CompanyDiscoveryCandidateStatus = CompanyDiscoveryCandidateStatus.REVIEWED
    promoted_company_id: int | None = None


@dataclass
class FakeCompany:
    id: int
    project_id: int


class FakeStagingRepository:
    def __init__(self, candidate: FakeCandidate | None) -> None:
        self.candidate = candidate
        self.get_calls: list[tuple[int, int]] = []
        self.link_calls: list[tuple[int, int, int]] = []
        self.get_error: BaseException | None = None
        self.link_error: BaseException | None = None

    def get_candidate_for_promotion(
        self, project_id: int, candidate_id: int
    ) -> FakeCandidate | None:
        self.get_calls.append((project_id, candidate_id))
        if self.get_error is not None:
            raise self.get_error
        if self.candidate is None:
            return None
        if self.candidate.project_id != project_id or self.candidate.id != candidate_id:
            return None
        return self.candidate

    def link_promoted_company(
        self, project_id: int, candidate_id: int, company_id: int
    ) -> FakeCandidate:
        self.link_calls.append((project_id, candidate_id, company_id))
        if self.link_error is not None:
            raise self.link_error
        if self.candidate is None:
            raise AssertionError("Candidate is missing.")
        self.candidate.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
        self.candidate.promoted_company_id = company_id
        return self.candidate


class FakeCompanyRepository:
    def __init__(self) -> None:
        self.existing_duplicate: FakeCompany | None = None
        self.companies: dict[int, FakeCompany] = {}
        self.get_calls: list[tuple[int, int]] = []
        self.duplicate_calls: list[tuple[int, str]] = []
        self.create_calls: list[dict[str, object]] = []
        self.create_error: BaseException | None = None
        self.duplicate_error: BaseException | None = None

    def get_for_project(self, project_id: int, company_id: int) -> FakeCompany | None:
        self.get_calls.append((project_id, company_id))
        company = self.companies.get(company_id)
        if company is None or company.project_id != project_id:
            return None
        return company

    def find_duplicate_by_website(self, project_id: int, website: str) -> FakeCompany | None:
        self.duplicate_calls.append((project_id, website))
        if self.duplicate_error is not None:
            raise self.duplicate_error
        return self.existing_duplicate

    def create_for_promotion(
        self,
        *,
        project_id: int,
        name: str,
        website: str | None,
        country: str | None,
        status: str = "NEW",
    ) -> FakeCompany:
        self.create_calls.append(
            {
                "project_id": project_id,
                "name": name,
                "website": website,
                "country": country,
                "status": status,
            }
        )
        if self.create_error is not None:
            raise self.create_error
        company = FakeCompany(id=91, project_id=project_id)
        self.companies[company.id] = company
        return company


def make_service(
    candidate: FakeCandidate | None = None,
) -> tuple[
    CompanyDiscoveryCandidatePromotionService,
    FakeStagingRepository,
    FakeCompanyRepository,
]:
    staging = FakeStagingRepository(candidate or FakeCandidate())
    companies = FakeCompanyRepository()
    service = CompanyDiscoveryCandidatePromotionService(
        cast(CandidatePromotionStagingRepository, staging),
        cast(CandidatePromotionCompanyRepository, companies),
    )
    return service, staging, companies


def test_reviewed_candidate_creates_company_and_links_exact_fields() -> None:
    service, staging, companies = make_service()

    result = service.promote(12, 34)

    assert result.company_id == 91
    assert result.previous_status == CompanyDiscoveryCandidateStatus.REVIEWED
    assert result.current_status == CompanyDiscoveryCandidateStatus.PROMOTED
    assert result.created_company is True
    assert result.changed is True
    assert staging.get_calls == [(12, 34)]
    assert staging.link_calls == [(12, 34, 91)]
    assert companies.duplicate_calls == [(12, "https://www.example.com/about")]
    assert companies.create_calls == [
        {
            "project_id": 12,
            "name": "Acme",
            "website": "https://www.example.com/about",
            "country": "US",
            "status": "NEW",
        }
    ]


def test_reviewed_candidate_reuses_project_website_duplicate() -> None:
    service, staging, companies = make_service()
    companies.existing_duplicate = FakeCompany(id=77, project_id=12)

    result = service.promote(12, 34)

    assert result.company_id == 77
    assert result.created_company is False
    assert result.changed is True
    assert companies.create_calls == []
    assert staging.link_calls == [(12, 34, 77)]


def test_candidate_without_website_does_not_use_weaker_duplicate_fallback() -> None:
    candidate = FakeCandidate(website=None)
    service, staging, companies = make_service(candidate)

    result = service.promote(12, 34)

    assert result.created_company is True
    assert companies.duplicate_calls == []
    assert companies.create_calls[0]["website"] is None
    assert staging.link_calls == [(12, 34, 91)]


@pytest.mark.parametrize(
    "status",
    [CompanyDiscoveryCandidateStatus.DISCOVERED, CompanyDiscoveryCandidateStatus.REJECTED],
)
def test_only_reviewed_candidate_can_newly_promote(
    status: CompanyDiscoveryCandidateStatus,
) -> None:
    service, staging, companies = make_service(FakeCandidate(candidate_status=status))

    with pytest.raises(CompanyDiscoveryCandidateNotEligibleError):
        service.promote(12, 34)

    assert staging.link_calls == []
    assert companies.duplicate_calls == []
    assert companies.create_calls == []


def test_valid_promoted_candidate_is_idempotent_without_repository_write() -> None:
    candidate = FakeCandidate(
        candidate_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        promoted_company_id=77,
    )
    service, staging, companies = make_service(candidate)
    companies.companies[77] = FakeCompany(id=77, project_id=12)

    result = service.promote(12, 34)

    assert result.company_id == 77
    assert result.created_company is False
    assert result.changed is False
    assert companies.get_calls == [(12, 77)]
    assert companies.duplicate_calls == []
    assert companies.create_calls == []
    assert staging.link_calls == []


@pytest.mark.parametrize("company_id", [None, 0, -1, True])
def test_promoted_candidate_with_invalid_company_id_is_inconsistent(
    company_id: int | None,
) -> None:
    candidate = FakeCandidate(
        candidate_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        promoted_company_id=company_id,
    )
    service, staging, companies = make_service(candidate)

    with pytest.raises(CompanyDiscoveryCandidatePromotionConsistencyError):
        service.promote(12, 34)

    assert companies.get_calls == []
    assert companies.create_calls == []
    assert staging.link_calls == []


def test_promoted_candidate_linked_to_other_project_is_inconsistent() -> None:
    candidate = FakeCandidate(
        candidate_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        promoted_company_id=77,
    )
    service, staging, companies = make_service(candidate)
    companies.companies[77] = FakeCompany(id=77, project_id=99)

    with pytest.raises(CompanyDiscoveryCandidatePromotionConsistencyError):
        service.promote(12, 34)

    assert staging.link_calls == []


def test_cross_project_and_missing_candidate_are_not_found() -> None:
    service, staging, companies = make_service(FakeCandidate(project_id=99))

    with pytest.raises(CompanyDiscoveryCandidatePromotionNotFoundError):
        service.promote(12, 34)

    assert staging.get_calls == [(12, 34)]
    assert companies.create_calls == []


@pytest.mark.parametrize(
    ("project_id", "candidate_id"),
    [(0, 34), (12, 0), (-1, 34), (12, -1), (True, 34), (12, True)],
)
def test_invalid_ids_are_rejected_before_repository_calls(
    project_id: int,
    candidate_id: int,
) -> None:
    service, staging, companies = make_service()

    with pytest.raises(CompanyDiscoveryCandidatePromotionInvalidDataError):
        service.promote(project_id, candidate_id)

    assert staging.get_calls == []
    assert companies.create_calls == []


@pytest.mark.parametrize(
    "candidate",
    [
        FakeCandidate(name=None),
        FakeCandidate(name="   "),
        FakeCandidate(name="<unsafe>"),
        FakeCandidate(name="n" * 256),
        FakeCandidate(website="https://example.com/" + "a" * 240),
        FakeCandidate(website="ftp://example.com"),
    ],
)
def test_invalid_candidate_data_creates_and_links_nothing(candidate: FakeCandidate) -> None:
    service, staging, companies = make_service(candidate)

    with pytest.raises(CompanyDiscoveryCandidatePromotionInvalidDataError):
        service.promote(12, 34)

    assert companies.create_calls == []
    assert staging.link_calls == []


def test_reviewed_candidate_with_existing_link_is_inconsistent() -> None:
    service, staging, companies = make_service(FakeCandidate(promoted_company_id=77))

    with pytest.raises(CompanyDiscoveryCandidatePromotionConsistencyError):
        service.promote(12, 34)

    assert companies.create_calls == []
    assert staging.link_calls == []


@pytest.mark.parametrize("boundary", ["create", "link"])
def test_persistence_failures_propagate(boundary: str) -> None:
    service, staging, companies = make_service()
    failure = RuntimeError("private persistence failure")
    if boundary == "create":
        companies.create_error = failure
    else:
        staging.link_error = failure

    with pytest.raises(RuntimeError, match="private persistence failure"):
        service.promote(12, 34)


class PromotionCriticalFailure(BaseException):
    pass


@pytest.mark.parametrize("boundary", ["candidate", "create", "link"])
def test_baseexception_propagates(boundary: str) -> None:
    service, staging, companies = make_service()
    failure = PromotionCriticalFailure("critical")
    if boundary == "candidate":
        staging.get_error = failure
    elif boundary == "create":
        companies.create_error = failure
    else:
        staging.link_error = failure

    with pytest.raises(PromotionCriticalFailure):
        service.promote(12, 34)


def test_promotion_result_enforces_state_invariants() -> None:
    with pytest.raises(ValueError):
        CompanyDiscoveryCandidatePromotionResult(
            candidate_id=34,
            project_id=12,
            company_id=91,
            previous_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            current_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            created_company=True,
            changed=True,
        )
    with pytest.raises(ValueError):
        CompanyDiscoveryCandidatePromotionResult(
            candidate_id=34,
            project_id=12,
            company_id=91,
            previous_status=CompanyDiscoveryCandidateStatus.PROMOTED,
            current_status=CompanyDiscoveryCandidateStatus.PROMOTED,
            created_company=True,
            changed=False,
        )


def test_service_has_no_ingestion_or_contact_dependency() -> None:
    source = getsource(CompanyDiscoveryCandidatePromotionService)
    assert "CompanyIngestionService" not in source
    assert "Contact" not in source

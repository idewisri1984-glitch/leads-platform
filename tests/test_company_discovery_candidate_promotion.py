import traceback
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
    def __init__(self, candidate: FakeCandidate | None, events: list[str]) -> None:
        self.candidate = candidate
        self.events = events
        self.get_calls: list[tuple[int, int]] = []
        self.link_calls: list[tuple[int, int, int]] = []
        self.get_error: BaseException | None = None
        self.link_error: BaseException | None = None

    def get_candidate_for_promotion(
        self, project_id: int, candidate_id: int
    ) -> FakeCandidate | None:
        self.events.append("candidate")
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
        self.events.append("link")
        self.link_calls.append((project_id, candidate_id, company_id))
        if self.link_error is not None:
            raise self.link_error
        if self.candidate is None:
            raise AssertionError("Candidate is missing.")
        self.candidate.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
        self.candidate.promoted_company_id = company_id
        return self.candidate


class FakeCompanyRepository:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.existing_duplicate: FakeCompany | None = None
        self.companies: dict[int, FakeCompany] = {}
        self.scope_calls: list[int] = []
        self.get_calls: list[tuple[int, int]] = []
        self.duplicate_calls: list[tuple[int, str]] = []
        self.create_calls: list[dict[str, object]] = []
        self.create_error: BaseException | None = None
        self.duplicate_error: BaseException | None = None
        self.scope_error: BaseException | None = None

    def acquire_promotion_scope(self, project_id: int) -> None:
        self.events.append("scope")
        self.scope_calls.append(project_id)
        if self.scope_error is not None:
            raise self.scope_error

    def get_for_project(self, project_id: int, company_id: int) -> FakeCompany | None:
        self.events.append("get")
        self.get_calls.append((project_id, company_id))
        company = self.companies.get(company_id)
        if company is None or company.project_id != project_id:
            return None
        return company

    def find_duplicate_by_website(self, project_id: int, website: str) -> FakeCompany | None:
        self.events.append("duplicate")
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
        self.events.append("create")
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
    events: list[str] = []
    staging = FakeStagingRepository(candidate or FakeCandidate(), events)
    companies = FakeCompanyRepository(events)
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
    assert companies.scope_calls == [12]
    assert companies.events == ["scope", "candidate", "duplicate", "create", "link"]
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
    assert companies.scope_calls == [12]
    assert companies.events == ["scope", "candidate", "get"]
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
    assert companies.scope_calls == []
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


@pytest.mark.parametrize("boundary", ["scope", "create", "link"])
def test_persistence_failures_propagate(boundary: str) -> None:
    service, staging, companies = make_service()
    failure = RuntimeError("private persistence failure")
    if boundary == "scope":
        companies.scope_error = failure
    elif boundary == "create":
        companies.create_error = failure
    else:
        staging.link_error = failure

    with pytest.raises(RuntimeError, match="private persistence failure"):
        service.promote(12, 34)


class PromotionCriticalFailure(BaseException):
    pass


@pytest.mark.parametrize("boundary", ["scope", "candidate", "create", "link"])
def test_baseexception_propagates(boundary: str) -> None:
    service, staging, companies = make_service()
    failure = PromotionCriticalFailure("critical")
    if boundary == "scope":
        companies.scope_error = failure
    elif boundary == "candidate":
        staging.get_error = failure
    elif boundary == "create":
        companies.create_error = failure
    else:
        staging.link_error = failure

    with pytest.raises(PromotionCriticalFailure):
        service.promote(12, 34)


def assert_sanitized_error(
    error: Exception,
    expected_message: str,
    markers: tuple[str, ...],
) -> None:
    assert str(error) == expected_message
    assert repr(error) == f"{type(error).__name__}({expected_message!r})"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False
    for chain in (True, False):
        formatted = "".join(traceback.format_exception(error, chain=chain))
        for marker in markers:
            assert marker not in formatted


def test_name_validation_error_chain_is_fully_sanitized() -> None:
    marker = "RAW_CANDIDATE_NAME_F7G1"
    service, _, _ = make_service(FakeCandidate(name=f"<{marker}>"))

    with pytest.raises(CompanyDiscoveryCandidatePromotionInvalidDataError) as captured:
        service.promote(12, 34)

    assert_sanitized_error(
        captured.value,
        "Candidate promotion data is invalid.",
        (marker,),
    )


def test_website_validation_error_chain_is_fully_sanitized() -> None:
    marker = "RAW_WEBSITE_CREDENTIALS_AND_PATH_F7G1"
    website = f"ftp://user:{marker}@example.com/{marker}"
    service, _, _ = make_service(FakeCandidate(website=website))

    with pytest.raises(CompanyDiscoveryCandidatePromotionInvalidDataError) as captured:
        service.promote(12, 34)

    assert_sanitized_error(
        captured.value,
        "Candidate promotion data is invalid.",
        (marker,),
    )


@pytest.mark.parametrize(
    ("failure_type", "marker"),
    [
        (ValueError, "RAW_DUPLICATE_NORMALIZATION_F7G1"),
        (TypeError, "RAW_DATABASE_NORMALIZATION_DETAIL_F7G1"),
    ],
)
def test_duplicate_lookup_error_chain_is_fully_sanitized(
    failure_type: type[Exception],
    marker: str,
) -> None:
    service, _, companies = make_service()
    companies.duplicate_error = failure_type(marker)

    with pytest.raises(CompanyDiscoveryCandidatePromotionInvalidDataError) as captured:
        service.promote(12, 34)

    assert_sanitized_error(
        captured.value,
        "Candidate promotion data is invalid.",
        (marker,),
    )


def test_country_validation_error_is_fully_sanitized() -> None:
    marker = "RAW_COUNTRY_F7G1"
    service, _, _ = make_service(FakeCandidate(country_code=marker + ("X" * 100)))

    with pytest.raises(CompanyDiscoveryCandidatePromotionInvalidDataError) as captured:
        service.promote(12, 34)

    assert_sanitized_error(
        captured.value,
        "Candidate promotion data is invalid.",
        (marker,),
    )


def test_missing_project_scope_error_chain_is_fully_sanitized() -> None:
    marker = "RAW_PROJECT_LOOKUP_F7G1"
    service, _, companies = make_service()
    companies.scope_error = ValueError(marker)

    with pytest.raises(CompanyDiscoveryCandidatePromotionNotFoundError) as captured:
        service.promote(12, 34)

    assert_sanitized_error(captured.value, "Candidate was not found.", (marker,))


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

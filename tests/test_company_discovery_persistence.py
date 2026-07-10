from typing import cast

import pytest

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.company_discovery import CompanyDiscoveryRequest, CompanyDiscoveryService
from app.modules.company_import.ingestion import CompanyIngestionService
from app.modules.company_import.schemas import (
    CompanyIngestionError,
    CompanyIngestionItem,
    CompanyIngestionResult,
)
from app.modules.project.repository import ProjectRepository
from app.providers.serpapi import SerpApiClient, SerpApiCompanyResult, SerpApiSearchResponse
from app.providers.serpapi.exceptions import (
    SerpApiConfigurationError,
    SerpApiRateLimitError,
    SerpApiRequestError,
)


class FakeSerpApiClient:
    def __init__(
        self,
        response: SerpApiSearchResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error

    def search_companies(
        self,
        *,
        query: str | None,
        country: str | None,
        city: str | None,
        industry: str | None,
        limit: int,
    ) -> SerpApiSearchResponse:
        if self.error is not None:
            raise self.error

        if self.response is None:
            raise AssertionError("FakeSerpApiClient response is required.")

        return self.response


def make_service(fake_client: FakeSerpApiClient) -> CompanyDiscoveryService:
    return CompanyDiscoveryService(cast(SerpApiClient, fake_client))


def make_request() -> CompanyDiscoveryRequest:
    return CompanyDiscoveryRequest(
        query="software companies",
        country="Indonesia",
        city="Bali",
        industry="SaaS",
        limit=10,
    )


def create_project(name: str = "Discovery Persistence Project") -> int:
    with SessionLocal() as session:
        return ProjectRepository(session).create(name).id


def make_response(
    results: list[SerpApiCompanyResult],
    query: str = "software companies SaaS Bali Indonesia",
) -> SerpApiSearchResponse:
    return SerpApiSearchResponse(query=query, results=results)


def test_persist_writes_discovered_companies() -> None:
    project_id = create_project()
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title="First",
                        link="first.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=2,
                        title="Second",
                        link="second.example",
                        snippet=None,
                        source=None,
                    ),
                ]
            )
        )
    )

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=project_id,
            request=make_request(),
        )

        companies = CompanyRepository(session).get_by_project(project_id)

    assert result.discovered == 2
    assert result.imported == 2
    assert result.skipped_duplicates == 0
    assert result.failed == 0
    assert result.errors == []
    assert result.rolled_back is False
    assert [company.name for company in companies] == ["First", "Second"]


def test_duplicates_are_skipped_through_company_ingestion_service() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        existing = CompanyRepository(session).create(
            project_id=project_id,
            name="Existing",
            website="https://www.example.com/about",
        )

    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title="Duplicate",
                        link="EXAMPLE.COM",
                        snippet=None,
                        source=None,
                    )
                ]
            )
        )
    )

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=project_id,
            request=make_request(),
        )

    assert result.discovered == 1
    assert result.imported == 0
    assert result.skipped_duplicates == 1
    assert result.failed == 0
    assert result.created_company_ids == []

    with SessionLocal() as session:
        companies = CompanyRepository(session).get_by_project(project_id)

    assert [company.id for company in companies] == [existing.id]


def test_blank_provider_title_becomes_adapter_error_and_valid_items_still_persist() -> None:
    project_id = create_project()
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title="Valid",
                        link="valid.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=2,
                        title=" ",
                        link="blank.example",
                        snippet=None,
                        source=None,
                    ),
                ]
            )
        )
    )

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=project_id,
            request=make_request(),
        )

        companies = CompanyRepository(session).get_by_project(project_id)

    assert result.discovered == 2
    assert result.imported == 1
    assert result.failed == 1
    assert len(result.errors) == 1
    assert result.errors[0].code == "invalid_discovery_result"
    assert result.errors[0].source_row_number == 2
    assert [company.name for company in companies] == ["Valid"]


def test_missing_project_returns_rolled_back_true_and_saves_nothing() -> None:
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title="Acme",
                        link="acme.example",
                        snippet=None,
                        source=None,
                    )
                ]
            )
        )
    )

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=999_999,
            request=make_request(),
        )

    assert result.discovered == 1
    assert result.imported == 0
    assert result.skipped_duplicates == 0
    assert result.failed == 1
    assert result.created_company_ids == []
    assert result.rolled_back is True
    assert result.errors[0].code == "project_not_found"

    with SessionLocal() as session:
        assert CompanyRepository(session).get_all() == []


def test_controlled_serpapi_configuration_error_propagates() -> None:
    service = make_service(FakeSerpApiClient(error=SerpApiConfigurationError("missing key")))

    with (
        SessionLocal() as session,
        pytest.raises(SerpApiConfigurationError, match="missing key"),
    ):
        service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=1,
            request=make_request(),
        )


def test_controlled_serpapi_rate_limit_error_propagates() -> None:
    service = make_service(FakeSerpApiClient(error=SerpApiRateLimitError("rate limit")))

    with (
        SessionLocal() as session,
        pytest.raises(SerpApiRateLimitError, match="rate limit"),
    ):
        service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=1,
            request=make_request(),
        )


def test_controlled_serpapi_request_error_propagates() -> None:
    service = make_service(FakeSerpApiClient(error=SerpApiRequestError("request failed")))

    with (
        SessionLocal() as session,
        pytest.raises(SerpApiRequestError, match="request failed"),
    ):
        service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=1,
            request=make_request(),
        )


def test_no_valid_discovery_items_does_not_call_ingestion_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title=" ",
                        link="blank.example",
                        snippet=None,
                        source=None,
                    )
                ]
            )
        )
    )

    def unexpected_ingest(
        self: CompanyIngestionService,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> None:
        raise AssertionError("Ingestion service must not be called without valid items.")

    monkeypatch.setattr(CompanyIngestionService, "ingest", unexpected_ingest)

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=999_999,
            request=make_request(),
        )

    assert result.discovered == 1
    assert result.imported == 0
    assert result.skipped_duplicates == 0
    assert result.failed == 1
    assert result.created_company_ids == []
    assert result.errors[0].code == "invalid_discovery_result"
    assert result.rolled_back is False


def test_adapter_errors_are_preserved_with_ingestion_errors() -> None:
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title=" ",
                        link="blank.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=2,
                        title="Invalid Website",
                        link="ftp://invalid.example",
                        snippet=None,
                        source=None,
                    ),
                ]
            )
        )
    )
    project_id = create_project()

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=project_id,
            request=make_request(),
        )

    assert result.discovered == 2
    assert result.imported == 0
    assert result.skipped_duplicates == 0
    assert result.failed == 2
    assert [error.code for error in result.errors] == [
        "invalid_discovery_result",
        "invalid_website",
    ]


def test_final_counters_are_correct_for_mixed_result() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        CompanyRepository(session).create(
            project_id=project_id,
            name="Existing",
            website="existing.example",
        )

    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title="New",
                        link="new.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=2,
                        title="Duplicate",
                        link="https://www.existing.example/about",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=3,
                        title=" ",
                        link="blank.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=4,
                        title="Invalid",
                        link="ftp://invalid.example",
                        snippet=None,
                        source=None,
                    ),
                ]
            )
        )
    )

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=project_id,
            request=make_request(),
        )

    assert result.discovered == 4
    assert result.imported == 1
    assert result.skipped_duplicates == 1
    assert result.failed == 2
    assert len(result.created_company_ids) == 1
    assert [error.code for error in result.errors] == [
        "invalid_discovery_result",
        "invalid_website",
    ]
    assert result.discovered == result.imported + result.skipped_duplicates + result.failed


def test_valid_items_are_passed_to_company_ingestion_service_without_pre_deduplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title="First",
                        link="same.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=2,
                        title="Second",
                        link="same.example",
                        snippet=None,
                        source=None,
                    ),
                ]
            )
        )
    )
    received_items: list[CompanyIngestionItem] = []

    def fake_ingest(
        self: CompanyIngestionService,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> CompanyIngestionResult:
        received_items.extend(items)
        return CompanyIngestionResult(
            total_rows=len(items),
            imported=len(items),
            skipped_duplicates=0,
            failed=0,
            created_company_ids=[100 + index for index in range(len(items))],
            duplicates=[],
            errors=[],
            rolled_back=False,
        )

    monkeypatch.setattr(CompanyIngestionService, "ingest", fake_ingest)

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=1,
            request=make_request(),
        )

    assert result.discovered == 2
    assert [item.name for item in received_items] == ["First", "Second"]
    assert [item.website for item in received_items] == ["same.example", "same.example"]


def test_adapter_errors_are_preserved_with_persistence_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(
        FakeSerpApiClient(
            make_response(
                [
                    SerpApiCompanyResult(
                        position=1,
                        title=" ",
                        link="blank.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=2,
                        title="Duplicate",
                        link="duplicate.example",
                        snippet=None,
                        source=None,
                    ),
                    SerpApiCompanyResult(
                        position=3,
                        title="New",
                        link="new.example",
                        snippet=None,
                        source=None,
                    ),
                ]
            )
        )
    )

    def fake_ingest(
        self: CompanyIngestionService,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> CompanyIngestionResult:
        return CompanyIngestionResult(
            total_rows=len(items),
            imported=0,
            skipped_duplicates=1,
            failed=1,
            created_company_ids=[],
            duplicates=[],
            errors=[
                CompanyIngestionError(
                    source_row_number=None,
                    code="persistence_error",
                    message="Company ingestion was rolled back due to a persistence error.",
                )
            ],
            rolled_back=True,
        )

    monkeypatch.setattr(CompanyIngestionService, "ingest", fake_ingest)

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=1,
            request=make_request(),
        )

    assert result.discovered == 3
    assert result.imported == 0
    assert result.skipped_duplicates == 1
    assert result.failed == 2
    assert result.created_company_ids == []
    assert result.rolled_back is True
    assert [error.code for error in result.errors] == [
        "invalid_discovery_result",
        "persistence_error",
    ]
    assert result.discovered == result.imported + result.skipped_duplicates + result.failed


def test_empty_provider_results_in_persistence_mode_does_not_call_ingestion_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(FakeSerpApiClient(make_response([], query="empty query")))

    def unexpected_ingest(
        self: CompanyIngestionService,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> None:
        raise AssertionError("Ingestion service must not be called without discovery items.")

    monkeypatch.setattr(CompanyIngestionService, "ingest", unexpected_ingest)

    with SessionLocal() as session:
        result = service.discover_and_ingest_from_serpapi(
            session=session,
            project_id=1,
            request=make_request(),
        )

    assert result.query == "empty query"
    assert result.discovered == 0
    assert result.imported == 0
    assert result.skipped_duplicates == 0
    assert result.failed == 0
    assert result.created_company_ids == []
    assert result.errors == []
    assert result.rolled_back is False
    assert result.discovered == result.imported + result.skipped_duplicates + result.failed

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import pytest

from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.profile_execution import SearchProfileDiscoveryService
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.schemas import (
    DiscoveryProviderResponse,
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryProviderError,
    SearchProfileDiscoveryQueryResult,
)
from app.modules.company_discovery.staging_orchestration import CompanyDiscoveryStagingService
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
)
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidatePreview,
)
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.query_generation import SearchProfileQueryGenerator
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)


class FakeProvider:
    def __init__(self, name: str = "fake") -> None:
        self.provider_name = name

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        raise AssertionError(f"provider search should not be called: {query.text}")


class FakeQueryGenerator:
    def __init__(self, preview: SearchQueryPreview) -> None:
        self.preview = preview
        self.calls: list[tuple[SearchProfileRead, SearchProfileRunOptions | None]] = []

    def generate_preview(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchQueryPreview:
        self.calls.append((profile, options))
        return self.preview


@dataclass
class DummyRun:
    id: int
    request_fingerprint: str


@dataclass
class DummyUpsertResult:
    created: bool = False
    updated: bool = False
    protected: bool = False


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.next_run_id = 1
        self.create_run_data: CompanyDiscoveryRunCreate | None = None
        self.update_run_data: CompanyDiscoveryRunUpdate | None = None
        self.upsert_results: list[DummyUpsertResult] = []

    def create_run(self, data: CompanyDiscoveryRunCreate) -> DummyRun:
        self.calls.append(("create_run", data))
        self.create_run_data = data
        run = DummyRun(self.next_run_id, data.request_snapshot.fingerprint())
        self.next_run_id += 1
        return run

    def upsert_candidate(
        self,
        *,
        project_id: int,
        run_id: int,
        data: CompanyDiscoveryCandidateCreate,
    ) -> DummyUpsertResult:
        self.calls.append(("upsert_candidate", project_id, run_id, data))
        if self.upsert_results:
            return self.upsert_results.pop(0)
        return DummyUpsertResult(created=True)

    def update_run(self, run_id: int, data: CompanyDiscoveryRunUpdate) -> None:
        self.calls.append(("update_run", run_id, data))
        self.update_run_data = data


class FakeDiscoveryExecutionService:
    def __init__(self, outcome: SearchProfileDiscoveryDryRunResult | BaseException) -> None:
        self.calls: list[
            tuple[SearchProfileRead, DiscoveryProvider, SearchProfileRunOptions | None]
        ] = []
        self.outcome = outcome

    def run_dry(
        self,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchProfileDiscoveryDryRunResult:
        self.calls.append((profile, provider, options))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def make_profile() -> SearchProfileRead:
    return SearchProfileRead(
        id=7,
        project_id=3,
        name="Buyer profile",
        description=None,
        product_or_service="Accounting software",
        target_customer_types=["accounting firms"],
        target_industries=[],
        positive_keywords=[],
        negative_keywords=[],
        countries=["Germany"],
        cities=["Berlin"],
        languages=[],
        query_templates=["{target_customer_type} {city} {country}"],
        result_limit=10,
        max_queries_per_run=3,
        total_result_ceiling=25,
        enabled=True,
    )


def make_query(
    number: int, *, country_code: str | None = "DE", country: str | None = "Germany"
) -> SearchQuery:
    return SearchQuery(
        text=f"accounting firms city-{number} {country or ''}".strip(),
        profile_id=7,
        profile_name="Buyer profile",
        language=None,
        country=country,
        city=f"city-{number}",
        source_template="{target_customer_type} {city} {country}",
        country_code=country_code,
        limit=10,
    )


def make_item(*, name: str, row: int, country: str, website: str) -> CompanyIngestionItem:
    return CompanyIngestionItem(
        source_row_number=row,
        name=name,
        website=website,
        country=country,
        city="Berlin",
    )


def make_query_result(
    query: SearchQuery,
    *,
    items: Sequence[CompanyIngestionItem] = (),
    provider_error: SearchProfileDiscoveryProviderError | None = None,
) -> SearchProfileDiscoveryQueryResult:
    return SearchProfileDiscoveryQueryResult(
        query=query,
        provider="fake",
        provider_result_count=len(items),
        adapted_item_count=len(items),
        adapter_error_count=0,
        provider_error=provider_error,
        items=list(items),
        adapter_errors=[],
    )


def make_dry_result(
    queries: Sequence[SearchQuery],
    query_results: Sequence[SearchProfileDiscoveryQueryResult],
    *,
    stopped_early: bool = False,
    stop_reason: str | None = None,
) -> SearchProfileDiscoveryDryRunResult:
    return SearchProfileDiscoveryDryRunResult(
        profile_id=7,
        profile_name="Buyer profile",
        provider="fake",
        query_count=len(queries),
        estimated_provider_requests=len(queries),
        executed_queries=len(query_results),
        total_provider_results=sum(result.provider_result_count for result in query_results),
        total_adapted_items=sum(result.adapted_item_count for result in query_results),
        total_adapter_errors=sum(result.adapter_error_count for result in query_results),
        total_provider_errors=sum(result.provider_error is not None for result in query_results),
        total_result_ceiling=25,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
        query_results=list(query_results),
    )


def make_run(
    service: FakeDiscoveryExecutionService,
    generator: FakeQueryGenerator,
) -> CompanyDiscoveryStagingService:
    return CompanyDiscoveryStagingService(
        repository=None,
        execution_service=cast(SearchProfileDiscoveryService, service),
        query_generator=cast(SearchProfileQueryGenerator, generator),
    )


def test_dry_run_succeeds_with_valid_candidates_without_mutation() -> None:
    query = make_query(1)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    generator = FakeQueryGenerator(preview)
    dry_result = make_dry_result(
        [query],
        [
            make_query_result(
                query,
                items=[
                    make_item(name="Acme", row=1, country="Germany", website="https://example.com")
                ],
            )
        ],
    )
    service = make_run(FakeDiscoveryExecutionService(dry_result), generator)
    profile = make_profile()
    repository = RecordingRepository()

    result = service.run(
        profile=profile,
        provider=FakeProvider(),
        options=SearchProfileRunOptions(),
        dry_run=True,
        repository=cast(CompanyDiscoveryStagingRepository, repository),
    )

    assert result.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert result.error_code is None
    assert result.run_persisted is False
    assert result.run_id is None
    assert result.candidate_upserts == 0
    assert repository.calls == []


def test_persist_mode_keeps_valid_candidate_and_rejects_markup_sibling() -> None:
    query = make_query(1)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = RecordingRepository()
    repository.upsert_results = [DummyUpsertResult(created=True)]
    dry_result = make_dry_result(
        [query],
        [
            make_query_result(
                query,
                items=[
                    make_item(
                        name="Acme",
                        row=1,
                        country="Germany",
                        website="https://example.com",
                    ),
                    make_item(
                        name="Bad <tag>",
                        row=2,
                        country="Germany",
                        website="https://example.com/bad",
                    ),
                ],
            )
        ],
    )

    result = make_run(
        FakeDiscoveryExecutionService(dry_result),
        FakeQueryGenerator(preview),
    ).run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=SearchProfileRunOptions(max_queries=1),
        dry_run=False,
        repository=cast(CompanyDiscoveryStagingRepository, repository),
    )

    assert result.status == CompanyDiscoveryRunStatus.PARTIAL
    assert result.error_code == "candidate_invalid"
    assert result.unique_candidate_count == 1
    assert result.candidate_upserts == 1
    upsert_calls = [call for call in repository.calls if call[0] == "upsert_candidate"]
    assert len(upsert_calls) == 1


def test_dry_run_partial_when_adapter_rejects_rows() -> None:
    query = make_query(1)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    query_results = [
        make_query_result(
            query,
            items=[
                make_item(name="Acme", row=1, country="Germany", website="https://example.com"),
                make_item(name="Bad", row=2, country="Germany", website="not-a-url"),
            ],
        )
    ]
    service = make_run(
        FakeDiscoveryExecutionService(make_dry_result([query], query_results)),
        FakeQueryGenerator(preview),
    )

    result = service.run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=None,
        dry_run=True,
        repository=None,
    )

    assert result.status == CompanyDiscoveryRunStatus.PARTIAL
    assert result.error_code == "candidate_invalid"
    assert result.unique_candidate_count == 1
    assert result.duplicate_candidate_count == 0
    assert len(result.candidates) == 1


def test_dry_run_merges_duplicate_candidate_identities_before_reporting() -> None:
    first_query = make_query(1)
    second_query = make_query(2, country_code="DE")
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[first_query, second_query],
    )
    query_results = [
        make_query_result(
            first_query,
            items=[
                make_item(
                    name="Acme", row=5, country="Germany", website="https://www.example.com/path"
                )
            ],
        ),
        make_query_result(
            second_query,
            items=[
                make_item(
                    name="Acme", row=2, country="Germany", website="https://example.com/other"
                )
            ],
        ),
    ]
    service = make_run(
        FakeDiscoveryExecutionService(make_dry_result([first_query, second_query], query_results)),
        FakeQueryGenerator(preview),
    )
    result = service.run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=None,
        dry_run=True,
        repository=None,
    )

    assert result.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert result.unique_candidate_count == 1
    assert result.duplicate_candidate_count == 1
    assert len(result.candidates) == 1
    assert isinstance(result.candidates[0], CompanyDiscoveryStagingCandidatePreview)
    assert result.candidates[0].best_position == 2


def test_dry_run_failure_when_all_queries_fail() -> None:
    query = make_query(1)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    query_results = [
        make_query_result(
            query,
            provider_error=SearchProfileDiscoveryProviderError(
                code="request_error",
                message="Discovery provider request failed.",
            ),
        )
    ]
    service = make_run(
        FakeDiscoveryExecutionService(make_dry_result([query], query_results)),
        FakeQueryGenerator(preview),
    )

    result = service.run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=None,
        dry_run=True,
        repository=None,
    )

    assert result.status == CompanyDiscoveryRunStatus.FAILED
    assert result.error_code == "request_error"


def test_persist_mode_creates_run_and_upserts_unique_candidates_once() -> None:
    query_a = make_query(1)
    query_b = make_query(2)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query_a, query_b],
    )
    query_results = [
        make_query_result(
            query_a,
            items=[
                make_item(
                    name="Acme", row=5, country="Germany", website="https://www.example.com/path"
                )
            ],
        ),
        make_query_result(
            query_b,
            items=[
                make_item(
                    name="Acme", row=2, country="Germany", website="https://example.com/other"
                )
            ],
        ),
    ]
    repository = RecordingRepository()
    service = make_run(
        FakeDiscoveryExecutionService(make_dry_result([query_a, query_b], query_results)),
        FakeQueryGenerator(preview),
    )

    result = service.run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=SearchProfileRunOptions(max_queries=2),
        dry_run=False,
        repository=cast(CompanyDiscoveryStagingRepository, repository),
    )

    assert repository.calls[0][0] == "create_run"
    assert repository.calls[-1][0] == "update_run"
    assert result.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert result.run_persisted is True
    assert result.run_id == 1
    assert result.candidate_upserts == 1
    assert result.candidates_created == 1
    assert result.unique_candidate_count == 1
    assert result.duplicate_candidate_count == 1


def test_persist_mode_stops_upserts_when_validation_is_invalid() -> None:
    query = make_query(1)
    bad_preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    bad_result = SearchProfileDiscoveryDryRunResult(
        profile_id=7,
        profile_name="Buyer profile",
        provider="mismatch",
        query_count=1,
        estimated_provider_requests=1,
        executed_queries=1,
        total_provider_results=0,
        total_adapted_items=0,
        total_adapter_errors=0,
        total_provider_errors=0,
        total_result_ceiling=25,
        stopped_early=False,
        stop_reason=None,
        query_results=[make_query_result(query)],
    )
    repository = RecordingRepository()
    service = make_run(
        FakeDiscoveryExecutionService(bad_result),
        FakeQueryGenerator(bad_preview),
    )

    result = service.run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=None,
        dry_run=False,
        repository=cast(CompanyDiscoveryStagingRepository, repository),
    )

    assert repository.calls[0][0] == "create_run"
    assert all(call[0] != "upsert_candidate" for call in repository.calls)
    assert result.status == CompanyDiscoveryRunStatus.FAILED
    assert result.error_code == "execution_invalid"
    assert result.run_id == 1


def test_unexpected_execution_exception_returns_failed_result() -> None:
    query = make_query(1)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = RecordingRepository()
    service = make_run(
        FakeDiscoveryExecutionService(RuntimeError("boom")),
        FakeQueryGenerator(preview),
    )

    result = service.run(
        profile=make_profile(),
        provider=FakeProvider(),
        options=None,
        dry_run=False,
        repository=cast(CompanyDiscoveryStagingRepository, repository),
    )

    assert result.status == CompanyDiscoveryRunStatus.FAILED
    assert result.error_code == "execution_failed"
    assert result.run_persisted is True
    assert result.run_id is not None
    assert repository.calls[0][0] == "create_run"
    assert repository.calls[-1][0] == "update_run"


def test_base_exception_is_not_caught_by_orchestrator() -> None:
    query = make_query(1)
    preview = SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )

    class Signal(SystemExit): ...

    service = make_run(
        FakeDiscoveryExecutionService(Signal(1)),
        FakeQueryGenerator(preview),
    )

    with pytest.raises(SystemExit):
        service.run(
            profile=make_profile(),
            provider=FakeProvider(),
            options=None,
            dry_run=True,
            repository=None,
        )

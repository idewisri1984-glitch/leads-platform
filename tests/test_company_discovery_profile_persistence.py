from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.modules import (
    SearchProfileDiscoveryPersistenceError as ExportedPersistenceError,
)
from app.modules import (
    SearchProfileDiscoveryPersistenceService as ExportedPersistenceService,
)
from app.modules import SearchProfileDiscoveryPersistResult as ExportedPersistResult
from app.modules.company_discovery import (
    DiscoveryProvider,
    SearchProfileDiscoveryAdapterError,
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryPersistenceError,
    SearchProfileDiscoveryPersistenceService,
    SearchProfileDiscoveryPersistResult,
    SearchProfileDiscoveryProviderError,
    SearchProfileDiscoveryQueryResult,
    SearchProfileDiscoveryService,
)
from app.modules.company_import import CompanyIngestionService
from app.modules.company_import.schemas import (
    CompanyIngestionDuplicate,
    CompanyIngestionError,
    CompanyIngestionItem,
    CompanyIngestionResult,
)
from app.modules.search_profile import SearchProfileRead, SearchProfileRunOptions, SearchQuery


class FakeProvider:
    provider_name = "fake"

    def search(self, query: SearchQuery) -> object:
        raise AssertionError("The fake discovery service must own provider execution.")


class RecordingDiscoveryService:
    def __init__(self, result: SearchProfileDiscoveryDryRunResult) -> None:
        self.result = result
        self.calls: list[
            tuple[SearchProfileRead, DiscoveryProvider, SearchProfileRunOptions | None]
        ] = []

    def run_dry(
        self,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchProfileDiscoveryDryRunResult:
        self.calls.append((profile, provider, options))
        return self.result


class RecordingIngestionService:
    def __init__(self, outcome: CompanyIngestionResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[int, list[CompanyIngestionItem]]] = []

    def ingest(
        self,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> CompanyIngestionResult:
        self.calls.append((project_id, items))

        if isinstance(self.outcome, Exception):
            raise self.outcome

        return self.outcome


class RecordingIngestionFactory:
    def __init__(self, service: RecordingIngestionService) -> None:
        self.service = service
        self.sessions: list[Session] = []

    def __call__(self, session: Session) -> CompanyIngestionService:
        self.sessions.append(session)
        return cast(CompanyIngestionService, self.service)


class ForbiddenWriteSession:
    def add(self, *args: object) -> None:
        raise AssertionError("Persistence service must not call session.add().")

    def flush(self, *args: object) -> None:
        raise AssertionError("Persistence service must not call session.flush().")

    def commit(self, *args: object) -> None:
        raise AssertionError("Persistence service must not call session.commit().")

    def rollback(self, *args: object) -> None:
        raise AssertionError("Persistence service must not call session.rollback().")


def make_profile() -> SearchProfileRead:
    return SearchProfileRead(
        id=7,
        project_id=31,
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


def make_query(number: int) -> SearchQuery:
    return SearchQuery(
        text=f"accounting firms city-{number} Germany",
        profile_id=7,
        profile_name="Buyer profile",
        country="Germany",
        city=f"city-{number}",
        source_template="{target_customer_type} {city} {country}",
        limit=10,
    )


def make_item(name: str, row: int) -> CompanyIngestionItem:
    return CompanyIngestionItem(
        source_row_number=row,
        name=name,
        website=f"https://{name.casefold()}.example",
        country="Germany",
        city="Berlin",
    )


def make_query_result(
    number: int,
    *,
    items: Sequence[CompanyIngestionItem] = (),
    adapter_errors: Sequence[SearchProfileDiscoveryAdapterError] = (),
    provider_error: SearchProfileDiscoveryProviderError | None = None,
) -> SearchProfileDiscoveryQueryResult:
    return SearchProfileDiscoveryQueryResult(
        query=make_query(number),
        provider="fake",
        provider_result_count=len(items) + len(adapter_errors),
        adapted_item_count=len(items),
        adapter_error_count=len(adapter_errors),
        provider_error=provider_error,
        items=list(items),
        adapter_errors=list(adapter_errors),
    )


def make_dry_result(
    query_results: Sequence[SearchProfileDiscoveryQueryResult],
    *,
    query_count: int | None = None,
    stopped_early: bool = False,
    stop_reason: str | None = None,
) -> SearchProfileDiscoveryDryRunResult:
    return SearchProfileDiscoveryDryRunResult(
        profile_id=7,
        profile_name="Buyer profile",
        provider="fake",
        query_count=query_count if query_count is not None else len(query_results),
        estimated_provider_requests=query_count if query_count is not None else len(query_results),
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


def make_ingestion_result(
    total_rows: int,
    *,
    rolled_back: bool = False,
) -> CompanyIngestionResult:
    if rolled_back:
        return CompanyIngestionResult(
            total_rows=total_rows,
            imported=0,
            skipped_duplicates=1,
            failed=total_rows - 1,
            created_company_ids=[],
            duplicates=[
                CompanyIngestionDuplicate(
                    source_row_number=1,
                    existing_company_id=99,
                    matched_by="website_hostname",
                    matched_value="duplicate.example",
                )
            ],
            errors=[
                CompanyIngestionError(
                    source_row_number=None,
                    code="persistence_error",
                    message="Company ingestion was rolled back.",
                )
            ],
            rolled_back=True,
        )

    return CompanyIngestionResult(
        total_rows=total_rows,
        imported=total_rows,
        skipped_duplicates=0,
        failed=0,
        created_company_ids=list(range(101, 101 + total_rows)),
        duplicates=[],
        errors=[],
        rolled_back=False,
    )


def make_service(
    dry_result: SearchProfileDiscoveryDryRunResult,
    ingestion_outcome: CompanyIngestionResult | Exception,
) -> tuple[
    SearchProfileDiscoveryPersistenceService,
    RecordingDiscoveryService,
    RecordingIngestionService,
    RecordingIngestionFactory,
]:
    discovery = RecordingDiscoveryService(dry_result)
    ingestion = RecordingIngestionService(ingestion_outcome)
    factory = RecordingIngestionFactory(ingestion)
    service = SearchProfileDiscoveryPersistenceService(
        discovery_service=cast(SearchProfileDiscoveryService, discovery),
        ingestion_service_factory=factory,
    )
    return service, discovery, ingestion, factory


def test_run_persist_composes_discovery_and_single_ordered_ingestion_call() -> None:
    first = make_item("First", 1)
    duplicate = make_item("Duplicate", 2)
    query_results = [
        make_query_result(1, items=[first, duplicate]),
        make_query_result(2, items=[duplicate]),
    ]
    dry_result = make_dry_result(query_results)
    ingestion_result = make_ingestion_result(3)
    service, discovery, ingestion, factory = make_service(dry_result, ingestion_result)
    profile = make_profile()
    provider = cast(DiscoveryProvider, FakeProvider())
    options = SearchProfileRunOptions(max_queries=2)
    session = cast(Session, ForbiddenWriteSession())

    result = service.run_persist(
        session=session,
        profile=profile,
        provider=provider,
        options=options,
    )

    assert discovery.calls == [(profile, provider, options)]
    assert factory.sessions == [session]
    assert len(ingestion.calls) == 1
    assert ingestion.calls[0][0] == profile.project_id
    assert ingestion.calls[0][1] == [first, duplicate, duplicate]
    assert ingestion.calls[0][1][1] is ingestion.calls[0][1][2]
    assert isinstance(result, SearchProfileDiscoveryPersistResult)
    assert result.ingestion_attempted is True
    assert result.total_items_submitted_to_ingestion == 3
    assert result.ingestion_result == ingestion_result
    assert (
        result.model_dump(
            exclude={
                "ingestion_attempted",
                "total_items_submitted_to_ingestion",
                "ingestion_result",
            }
        )
        == dry_result.model_dump()
    )
    assert result.query_results == query_results


@pytest.mark.parametrize(
    "dry_result",
    [
        make_dry_result([make_query_result(1)]),
        make_dry_result(
            [
                make_query_result(
                    1,
                    adapter_errors=[
                        SearchProfileDiscoveryAdapterError(
                            position=1,
                            message="Discovery result could not be adapted.",
                        )
                    ],
                )
            ]
        ),
        make_dry_result(
            [
                make_query_result(
                    1,
                    provider_error=SearchProfileDiscoveryProviderError(
                        code="configuration_error",
                        message="Discovery provider is not configured.",
                    ),
                )
            ],
            stopped_early=True,
            stop_reason="configuration_error",
        ),
    ],
)
def test_zero_adapted_items_returns_no_op_without_creating_ingestion_service(
    dry_result: SearchProfileDiscoveryDryRunResult,
) -> None:
    service, _, ingestion, factory = make_service(dry_result, make_ingestion_result(0))

    result = service.run_persist(
        session=cast(Session, ForbiddenWriteSession()),
        profile=make_profile(),
        provider=cast(DiscoveryProvider, FakeProvider()),
    )

    assert factory.sessions == []
    assert ingestion.calls == []
    assert result.ingestion_attempted is False
    assert result.total_items_submitted_to_ingestion == 0
    assert result.ingestion_result is None
    assert result.total_provider_errors == dry_result.total_provider_errors
    assert result.total_adapter_errors == dry_result.total_adapter_errors


@pytest.mark.parametrize(
    ("provider_error", "stopped_early", "stop_reason"),
    [
        (
            SearchProfileDiscoveryProviderError(
                code="rate_limit_error",
                message="Discovery provider rate limit exceeded.",
            ),
            True,
            "rate_limit_error",
        ),
        (
            SearchProfileDiscoveryProviderError(
                code="provider_error",
                message="Discovery provider failed.",
            ),
            True,
            "provider_error",
        ),
        (
            SearchProfileDiscoveryProviderError(
                code="request_error",
                message="Discovery provider request failed.",
            ),
            False,
            None,
        ),
        (
            SearchProfileDiscoveryProviderError(
                code="response_error",
                message="Discovery provider response was invalid.",
            ),
            False,
            None,
        ),
    ],
)
def test_provider_errors_do_not_block_ingestion_of_collected_items(
    provider_error: SearchProfileDiscoveryProviderError,
    stopped_early: bool,
    stop_reason: str | None,
) -> None:
    valid_item = make_item("Valid", 1)
    dry_result = make_dry_result(
        [
            make_query_result(1, items=[valid_item]),
            make_query_result(2, provider_error=provider_error),
        ],
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )
    service, _, ingestion, _ = make_service(dry_result, make_ingestion_result(1))

    result = service.run_persist(
        session=cast(Session, ForbiddenWriteSession()),
        profile=make_profile(),
        provider=cast(DiscoveryProvider, FakeProvider()),
    )

    assert ingestion.calls[0][1] == [valid_item]
    assert result.total_provider_errors == 1
    assert result.stopped_early is stopped_early


def test_adapter_errors_are_preserved_but_not_submitted() -> None:
    valid_item = make_item("Valid", 1)
    adapter_error = SearchProfileDiscoveryAdapterError(
        position=2,
        message="Discovery result could not be adapted.",
    )
    dry_result = make_dry_result(
        [make_query_result(1, items=[valid_item], adapter_errors=[adapter_error])]
    )
    service, _, ingestion, _ = make_service(dry_result, make_ingestion_result(1))

    result = service.run_persist(
        session=cast(Session, ForbiddenWriteSession()),
        profile=make_profile(),
        provider=cast(DiscoveryProvider, FakeProvider()),
    )

    assert ingestion.calls[0][1] == [valid_item]
    assert result.total_adapter_errors == 1
    assert result.query_results[0].adapter_errors == [adapter_error]


def test_rolled_back_ingestion_result_is_preserved_without_session_rollback() -> None:
    item = make_item("Duplicate", 1)
    ingestion_result = make_ingestion_result(2, rolled_back=True)
    dry_result = make_dry_result([make_query_result(1, items=[item, item])])
    service, _, _, _ = make_service(dry_result, ingestion_result)

    result = service.run_persist(
        session=cast(Session, ForbiddenWriteSession()),
        profile=make_profile(),
        provider=cast(DiscoveryProvider, FakeProvider()),
    )

    assert result.ingestion_result is not None
    assert result.ingestion_result.rolled_back is True
    assert result.ingestion_result.created_company_ids == []
    assert result.ingestion_result.duplicates == ingestion_result.duplicates
    assert result.ingestion_result.errors == ingestion_result.errors


def test_unexpected_ingestion_exception_is_fixed_safe_and_has_no_cause() -> None:
    raw_message = "SQL failed with sk-fake-secret at sqlite:///private.db"
    dry_result = make_dry_result([make_query_result(1, items=[make_item("Valid", 1)])])
    service, _, _, _ = make_service(dry_result, RuntimeError(raw_message))

    with pytest.raises(SearchProfileDiscoveryPersistenceError) as captured:
        service.run_persist(
            session=cast(Session, ForbiddenWriteSession()),
            profile=make_profile(),
            provider=cast(DiscoveryProvider, FakeProvider()),
        )

    assert str(captured.value) == "Company ingestion failed."
    assert raw_message not in str(captured.value)
    assert captured.value.__cause__ is None


def valid_persist_values() -> dict[str, object]:
    dry_result = make_dry_result([make_query_result(1, items=[make_item("Valid", 1)])])
    return {
        **dry_result.model_dump(),
        "ingestion_attempted": True,
        "total_items_submitted_to_ingestion": 1,
        "ingestion_result": make_ingestion_result(1),
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"ingestion_result": None},
        {"ingestion_attempted": False},
        {"total_items_submitted_to_ingestion": 0},
        {"total_items_submitted_to_ingestion": 2},
    ],
)
def test_persist_result_rejects_invalid_ingestion_invariants(
    updates: dict[str, object],
) -> None:
    values = {**valid_persist_values(), **updates}

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryPersistResult.model_validate(values)


def test_no_op_requires_zero_submitted_items_and_no_ingestion_result() -> None:
    dry_result = make_dry_result([])
    values = {
        **dry_result.model_dump(),
        "ingestion_attempted": False,
        "total_items_submitted_to_ingestion": 1,
        "ingestion_result": None,
    }

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryPersistResult.model_validate(values)


def test_persist_result_list_defaults_are_independent() -> None:
    values = {
        **make_dry_result([]).model_dump(),
        "ingestion_attempted": False,
        "total_items_submitted_to_ingestion": 0,
    }
    first = SearchProfileDiscoveryPersistResult.model_validate(values)
    second = SearchProfileDiscoveryPersistResult.model_validate(values)

    first.query_results.append(make_query_result(1))

    assert second.query_results == []


def test_exports_and_module_boundaries() -> None:
    assert ExportedPersistenceService is SearchProfileDiscoveryPersistenceService
    assert ExportedPersistenceError is SearchProfileDiscoveryPersistenceError
    assert ExportedPersistResult is SearchProfileDiscoveryPersistResult

    root = Path(__file__).parents[1]
    persistence_source = (root / "app/modules/company_discovery/profile_persistence.py").read_text(
        encoding="utf-8"
    )
    search_profile_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "app/modules/search_profile").glob("*.py")
    )

    for forbidden in (
        "SerpApiClient",
        "SerpApiDiscoveryProvider",
        "SearchProfileRepository",
        "SearchProfileService",
        "app.modules.company.models",
        "CompanyDiscoveryService",
        "session.add",
        "session.flush",
        "session.commit",
        "session.rollback",
    ):
        assert forbidden not in persistence_source

    assert "profile_persistence" not in search_profile_sources

import inspect
import socket
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

import app.modules.company_discovery.profile_execution as profile_execution_module
from app.modules import (
    SearchProfileDiscoveryAdapterError as ExportedAdapterError,
)
from app.modules import (
    SearchProfileDiscoveryDryRunResult as ExportedDryRunResult,
)
from app.modules import (
    SearchProfileDiscoveryExecutionError as ExportedExecutionError,
)
from app.modules import (
    SearchProfileDiscoveryProviderError as ExportedProviderError,
)
from app.modules import (
    SearchProfileDiscoveryQueryResult as ExportedQueryResult,
)
from app.modules import SearchProfileDiscoveryService as ExportedService
from app.modules.company_discovery import (
    DiscoveryProviderAuthenticationError,
    DiscoveryProviderConfigurationError,
    DiscoveryProviderError,
    DiscoveryProviderQuotaExceededError,
    DiscoveryProviderRateLimitError,
    DiscoveryProviderRequestError,
    DiscoveryProviderResponse,
    DiscoveryProviderResponseError,
    DiscoveryProviderResponseTooLargeError,
    DiscoveryProviderResult,
    DiscoveryResultAdapterError,
    SearchProfileDiscoveryAdapterError,
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryExecutionError,
    SearchProfileDiscoveryProviderError,
    SearchProfileDiscoveryQueryResult,
    SearchProfileDiscoveryService,
    provider_result_to_ingestion_item,
)
from app.modules.company_discovery.schemas import ProviderErrorCode
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.query_generation import SearchProfileQueryGenerator
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)

_FAKE_API_KEY = "fake-secret-api-key"
_RAW_PAYLOAD = "raw payload marker"


class RecordingQueryGenerator:
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


class FakeProvider:
    def __init__(
        self,
        outcomes: Sequence[DiscoveryProviderResponse | BaseException],
        *,
        provider_name: str = "fake",
    ) -> None:
        self.outcomes = list(outcomes)
        self._provider_name = provider_name
        self.provider_name_reads = 0
        self.queries: list[SearchQuery] = []

    @property
    def provider_name(self) -> str:
        self.provider_name_reads += 1
        return self._provider_name

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        self.queries.append(query)
        outcome = self.outcomes[len(self.queries) - 1]

        if isinstance(outcome, BaseException):
            raise outcome

        return outcome


class FatalProviderSignal(BaseException):
    """Non-standard provider signal intentionally not wrapped by service."""


def make_profile(*, enabled: bool = True) -> SearchProfileRead:
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
        enabled=enabled,
    )


def make_query(number: int, *, limit: int = 10) -> SearchQuery:
    return SearchQuery(
        text=f"accounting firms city-{number} Germany",
        profile_id=7,
        profile_name="Buyer profile",
        country="Germany",
        city=f"city-{number}",
        source_template="{target_customer_type} {city} {country}",
        limit=limit,
    )


def make_preview(
    queries: list[SearchQuery],
    *,
    total_result_ceiling: int = 25,
) -> SearchQueryPreview:
    return SearchQueryPreview(
        profile_id=7,
        profile_name="Buyer profile",
        query_count=len(queries),
        estimated_provider_requests=len(queries),
        result_limit_per_query=10,
        total_result_ceiling=total_result_ceiling,
        queries=queries,
    )


def make_response(
    query: SearchQuery,
    results: list[DiscoveryProviderResult] | None = None,
) -> DiscoveryProviderResponse:
    return DiscoveryProviderResponse(
        provider="fake",
        query=query.text,
        results=results or [],
        total_results=None,
    )


def make_service(
    preview: SearchQueryPreview,
) -> tuple[SearchProfileDiscoveryService, RecordingQueryGenerator]:
    generator = RecordingQueryGenerator(preview)
    service = SearchProfileDiscoveryService(cast(SearchProfileQueryGenerator, generator))
    return service, generator


def run_with(
    queries: list[SearchQuery],
    outcomes: Sequence[DiscoveryProviderResponse | Exception],
    *,
    profile: SearchProfileRead | None = None,
    options: SearchProfileRunOptions | None = None,
    total_result_ceiling: int = 25,
    provider_name: str = "fake",
) -> tuple[
    SearchProfileDiscoveryDryRunResult,
    RecordingQueryGenerator,
    FakeProvider,
]:
    service, generator = make_service(
        make_preview(queries, total_result_ceiling=total_result_ceiling)
    )
    provider = FakeProvider(outcomes, provider_name=provider_name)
    report = service.run_dry(profile or make_profile(), provider, options)
    return report, generator, provider


def test_basic_execution_preserves_order_limits_context_duplicates_and_inputs() -> None:
    queries = [make_query(1, limit=10), make_query(2, limit=5)]
    duplicate = DiscoveryProviderResult(
        title="Example Company",
        link="https://example.com",
        source="Directory",
        snippet="Accounting firm",
        position=1,
    )
    responses = [
        make_response(queries[0], [duplicate, duplicate.model_copy()]),
        make_response(queries[1], [DiscoveryProviderResult(title="Second Company")]),
    ]
    profile = make_profile()
    options = SearchProfileRunOptions(max_queries=2, total_result_ceiling=15)
    original_profile = profile.model_dump()
    original_options = options.model_dump()
    original_queries = [query.model_dump() for query in queries]
    original_responses = [response.model_dump() for response in responses]

    report, generator, provider = run_with(
        queries,
        responses,
        profile=profile,
        options=options,
        total_result_ceiling=15,
    )

    assert generator.calls == [(profile, options)]
    assert provider.queries == queries
    assert [query.limit for query in provider.queries] == [10, 5]
    assert provider.provider_name_reads == 1
    assert report.query_count == 2
    assert report.estimated_provider_requests == 2
    assert report.executed_queries == 2
    assert report.total_provider_results == 3
    assert report.total_adapted_items == 3
    assert report.total_adapter_errors == 0
    assert report.total_provider_errors == 0
    assert report.total_result_ceiling == 15
    assert report.stopped_early is False
    assert report.stop_reason is None
    first_items = report.query_results[0].items
    assert len(first_items) == 2
    assert first_items[0] == first_items[1]
    assert first_items[0] is not first_items[1]
    assert first_items[0].name == "Example Company"
    assert first_items[0].website == "https://example.com"
    assert first_items[0].country == "Germany"
    assert first_items[0].city == "city-1"
    assert isinstance(first_items[0], CompanyIngestionItem)
    assert profile.model_dump() == original_profile
    assert options.model_dump() == original_options
    assert [query.model_dump() for query in queries] == original_queries
    assert [response.model_dump() for response in responses] == original_responses


def test_empty_provider_response_produces_zero_query_counts() -> None:
    query = make_query(1)

    report, _, _ = run_with([query], [make_response(query)])

    query_result = report.query_results[0]
    assert query_result.provider_result_count == 0
    assert query_result.adapted_item_count == 0
    assert query_result.adapter_error_count == 0
    assert query_result.items == []
    assert query_result.adapter_errors == []


def test_adapter_errors_are_safe_and_do_not_stop_remaining_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = make_query(1)
    bad = DiscoveryProviderResult(
        title=f"{_RAW_PAYLOAD} {_FAKE_API_KEY}",
        position=2,
    )
    good = DiscoveryProviderResult(title="Good Company", position=3)
    real_adapter = provider_result_to_ingestion_item

    def fake_adapter(
        result: DiscoveryProviderResult,
        *,
        query: SearchQuery,
        provider_name: str,
    ) -> CompanyIngestionItem:
        if result.position == 2:
            raise DiscoveryResultAdapterError(f"unsafe {_RAW_PAYLOAD} {_FAKE_API_KEY}")

        return real_adapter(result, query=query, provider_name=provider_name)

    monkeypatch.setattr(
        profile_execution_module,
        "provider_result_to_ingestion_item",
        fake_adapter,
    )

    report, _, _ = run_with([query], [make_response(query, [bad, good])])

    query_result = report.query_results[0]
    assert query_result.provider_result_count == 2
    assert query_result.adapted_item_count == 1
    assert query_result.items[0].name == "Good Company"
    assert query_result.adapter_error_count == 1
    assert query_result.adapter_errors == [
        SearchProfileDiscoveryAdapterError(
            position=2,
            message="Discovery result could not be adapted.",
        )
    ]
    serialized_error = query_result.adapter_errors[0].model_dump_json()
    assert _RAW_PAYLOAD not in serialized_error
    assert _FAKE_API_KEY not in serialized_error
    assert report.total_adapter_errors == 1


def test_query_remains_when_all_results_fail_adaptation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = make_query(1)
    results = [
        DiscoveryProviderResult(title="Bad One", position=1),
        DiscoveryProviderResult(title="Bad Two", position=2),
    ]

    def fail_adapter(*args: object, **kwargs: object) -> CompanyIngestionItem:
        raise DiscoveryResultAdapterError("unsafe internal detail")

    monkeypatch.setattr(
        profile_execution_module,
        "provider_result_to_ingestion_item",
        fail_adapter,
    )

    report, _, _ = run_with([query], [make_response(query, results)])

    assert report.executed_queries == 1
    assert report.query_results[0].provider_result_count == 2
    assert report.query_results[0].adapted_item_count == 0
    assert report.query_results[0].adapter_error_count == 2


@pytest.mark.parametrize(
    ("provider_error", "code", "message", "stop_reason"),
    [
        (
            DiscoveryProviderAuthenticationError("unsafe detail"),
            "authentication_error",
            "Discovery provider authentication failed.",
            "authentication_error",
        ),
        (
            DiscoveryProviderConfigurationError("unsafe detail"),
            "configuration_error",
            "Discovery provider is not configured.",
            "configuration_error",
        ),
        (
            DiscoveryProviderQuotaExceededError("unsafe detail"),
            "quota_exceeded",
            "Discovery provider quota was exceeded.",
            "quota_exceeded",
        ),
        (
            DiscoveryProviderRateLimitError("unsafe detail"),
            "rate_limit_error",
            "Discovery provider rate limit exceeded.",
            "rate_limit_error",
        ),
        (
            DiscoveryProviderResponseTooLargeError("unsafe detail"),
            "response_too_large",
            "Discovery provider response exceeded the allowed size.",
            "response_too_large",
        ),
        (
            DiscoveryProviderError("unsafe detail"),
            "provider_error",
            "Discovery provider failed.",
            "provider_error",
        ),
    ],
)
def test_terminal_provider_error_records_current_query_and_stops(
    provider_error: Exception,
    code: str,
    message: str,
    stop_reason: str,
) -> None:
    queries = [make_query(1), make_query(2)]

    report, _, provider = run_with(
        queries,
        [provider_error, make_response(queries[1])],
    )

    assert provider.queries == [queries[0]]
    assert report.query_count == 2
    assert report.executed_queries == 1
    assert report.executed_queries < report.query_count
    assert report.total_provider_errors == 1
    assert report.stopped_early is True
    assert report.stop_reason == stop_reason
    error = report.query_results[0].provider_error
    assert error is not None
    assert error.code == code
    assert error.message == message
    assert "unsafe detail" not in error.message


@pytest.mark.parametrize(
    ("provider_error", "code", "message"),
    [
        (
            DiscoveryProviderRequestError("unsafe detail"),
            "request_error",
            "Discovery provider request failed.",
        ),
        (
            DiscoveryProviderResponseError("unsafe detail"),
            "response_error",
            "Discovery provider response was invalid.",
        ),
    ],
)
def test_nonterminal_provider_error_records_query_and_continues(
    provider_error: Exception,
    code: str,
    message: str,
) -> None:
    queries = [make_query(1), make_query(2)]
    success = make_response(
        queries[1],
        [DiscoveryProviderResult(title="Recovered Company")],
    )

    report, _, provider = run_with(queries, [provider_error, success])

    assert provider.queries == queries
    assert report.executed_queries == 2
    assert report.total_provider_errors == 1
    assert report.total_provider_results == 1
    assert report.total_adapted_items == 1
    assert report.stopped_early is False
    assert report.stop_reason is None
    error = report.query_results[0].provider_error
    assert error is not None
    assert error.code == code
    assert error.message == message


def test_unknown_provider_exception_is_not_swallowed() -> None:
    query = make_query(1)
    service, _ = make_service(make_preview([query]))
    provider = FakeProvider([RuntimeError("programming error")])

    with pytest.raises(RuntimeError, match="programming error"):
        service.run_dry(make_profile(), provider)


def test_fatal_provider_signal_is_not_wrapped_or_stopped_safely() -> None:
    queries = [make_query(1), make_query(2)]
    service, _ = make_service(make_preview(queries))
    provider = FakeProvider([FatalProviderSignal("fatal signal"), make_response(queries[1])])

    with pytest.raises(FatalProviderSignal, match="fatal signal"):
        service.run_dry(make_profile(), provider)

    assert provider.queries == [queries[0]]


def test_disabled_profile_does_not_call_generator_or_provider() -> None:
    query = make_query(1)
    service, generator = make_service(make_preview([query]))
    provider = FakeProvider([make_response(query)])

    with pytest.raises(SearchProfileDiscoveryExecutionError) as error:
        service.run_dry(make_profile(enabled=False), provider)

    assert str(error.value) == "Search profile is disabled."
    assert generator.calls == []
    assert provider.provider_name_reads == 0
    assert provider.queries == []


def test_blank_provider_name_does_not_call_generator_or_search() -> None:
    query = make_query(1)
    service, generator = make_service(make_preview([query]))
    provider = FakeProvider([make_response(query)], provider_name="   ")

    with pytest.raises(SearchProfileDiscoveryExecutionError) as error:
        service.run_dry(make_profile(), provider)

    assert str(error.value) == "Discovery provider name is invalid."
    assert generator.calls == []
    assert provider.provider_name_reads == 1
    assert provider.queries == []


def make_query_result() -> SearchProfileDiscoveryQueryResult:
    return SearchProfileDiscoveryQueryResult(
        query=make_query(1),
        provider="fake",
        provider_result_count=0,
        adapted_item_count=0,
        adapter_error_count=0,
    )


def make_dry_run_values() -> dict[str, object]:
    query_result = make_query_result()
    return {
        "profile_id": 7,
        "profile_name": "Buyer profile",
        "provider": "fake",
        "query_count": 1,
        "estimated_provider_requests": 1,
        "executed_queries": 1,
        "total_provider_results": 0,
        "total_adapted_items": 0,
        "total_adapter_errors": 0,
        "total_provider_errors": 0,
        "total_result_ceiling": 25,
        "stopped_early": False,
        "stop_reason": None,
        "query_results": [query_result],
    }


def test_error_schema_messages_are_normalized_and_required() -> None:
    provider_error = SearchProfileDiscoveryProviderError(
        code="request_error",
        message="  Request failed.  ",
    )
    adapter_error = SearchProfileDiscoveryAdapterError(
        position=1,
        message="  Adaptation failed.  ",
    )

    assert provider_error.message == "Request failed."
    assert adapter_error.message == "Adaptation failed."

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryProviderError(code="request_error", message="   ")

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryAdapterError(position=0, message="Failed.")


def _set_adapted_item_count(values: dict[str, object]) -> None:
    values["adapted_item_count"] = 1


def _set_adapter_error_count(values: dict[str, object]) -> None:
    values["adapter_error_count"] = 1


@pytest.mark.parametrize("change", [_set_adapted_item_count, _set_adapter_error_count])
def test_query_result_rejects_inconsistent_counters(
    change: Callable[[dict[str, object]], None],
) -> None:
    values: dict[str, object] = {
        "query": make_query(1),
        "provider": "fake",
        "provider_result_count": 0,
        "adapted_item_count": 0,
        "adapter_error_count": 0,
        "items": [],
        "adapter_errors": [],
    }
    change(values)

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryQueryResult.model_validate(values)


def _set_executed_queries_zero(values: dict[str, object]) -> None:
    values["executed_queries"] = 0


def _set_total_provider_results_one(values: dict[str, object]) -> None:
    values["total_provider_results"] = 1


def _set_total_adapted_items_one(values: dict[str, object]) -> None:
    values["total_adapted_items"] = 1


def _set_total_adapter_errors_one(values: dict[str, object]) -> None:
    values["total_adapter_errors"] = 1


def _set_total_provider_errors_one(values: dict[str, object]) -> None:
    values["total_provider_errors"] = 1


def _set_query_count_zero(values: dict[str, object]) -> None:
    values["query_count"] = 0


def _set_estimated_provider_requests_two(values: dict[str, object]) -> None:
    values["estimated_provider_requests"] = 2


@pytest.mark.parametrize(
    "change",
    [
        _set_executed_queries_zero,
        _set_total_provider_results_one,
        _set_total_adapted_items_one,
        _set_total_adapter_errors_one,
        _set_total_provider_errors_one,
        _set_query_count_zero,
        _set_estimated_provider_requests_two,
    ],
)
def test_dry_run_result_rejects_inconsistent_totals(
    change: Callable[[dict[str, object]], None],
) -> None:
    values = make_dry_run_values()
    change(values)

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryDryRunResult.model_validate(values)


def test_dry_run_result_requires_consistent_stop_state() -> None:
    stopped_without_reason = make_dry_run_values()
    stopped_without_reason["stopped_early"] = True

    reason_without_stop = make_dry_run_values()
    reason_without_stop["stop_reason"] = "rate_limit_error"

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryDryRunResult.model_validate(stopped_without_reason)

    with pytest.raises(ValidationError):
        SearchProfileDiscoveryDryRunResult.model_validate(reason_without_stop)


@pytest.mark.parametrize(
    "value",
    [
        "authentication_error",
        "configuration_error",
        "quota_exceeded",
        "rate_limit_error",
        "response_error",
        "response_too_large",
        "provider_error",
    ],
)
def test_known_provider_error_code_is_accepted(value: str) -> None:
    error = SearchProfileDiscoveryProviderError(code=value, message="ok")
    assert error.code == value


@pytest.mark.parametrize(
    "value",
    [
        "authentication_error",
        "configuration_error",
        "quota_exceeded",
        "rate_limit_error",
        "response_too_large",
        "provider_error",
    ],
)
def test_known_stop_reason_is_accepted(value: str) -> None:
    values = make_dry_run_values()
    values.update(stopped_early=True, stop_reason=value)
    report = SearchProfileDiscoveryDryRunResult.model_validate(values)
    assert report.stop_reason == value


@pytest.mark.parametrize("value", [42, True, None, "done", "success"])
def test_unknown_provider_error_code_is_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        SearchProfileDiscoveryProviderError(code=cast(ProviderErrorCode, value), message="ok")


@pytest.mark.parametrize("value", [42, True, None, "done", "success"])
def test_unknown_stop_reason_is_rejected(value: object) -> None:
    values = make_dry_run_values()
    values.update(stopped_early=True, stop_reason=value)
    with pytest.raises(ValidationError):
        SearchProfileDiscoveryDryRunResult.model_validate(values)


def test_schema_list_defaults_are_independent() -> None:
    first_query = make_query_result()
    second_query = make_query_result()
    first_query.items.append(CompanyIngestionItem(name="Example"))
    first_query.adapter_errors.append(SearchProfileDiscoveryAdapterError(message="Failed."))

    first_report_values = make_dry_run_values()
    first_report_values.update(
        query_count=0,
        estimated_provider_requests=0,
        executed_queries=0,
        query_results=[],
    )
    second_report_values = dict(first_report_values)
    first_report = SearchProfileDiscoveryDryRunResult.model_validate(first_report_values)
    second_report = SearchProfileDiscoveryDryRunResult.model_validate(second_report_values)
    first_report.query_results.append(make_query_result())

    assert second_query.items == []
    assert second_query.adapter_errors == []
    assert second_report.query_results == []


def test_exports_are_available_without_identity_changes() -> None:
    assert ExportedService is SearchProfileDiscoveryService
    assert ExportedExecutionError is SearchProfileDiscoveryExecutionError
    assert ExportedProviderError is SearchProfileDiscoveryProviderError
    assert ExportedAdapterError is SearchProfileDiscoveryAdapterError
    assert ExportedQueryResult is SearchProfileDiscoveryQueryResult
    assert ExportedDryRunResult is SearchProfileDiscoveryDryRunResult


@pytest.mark.parametrize(
    "forbidden_dependency",
    [
        "sqlalchemy",
        "SessionLocal",
        "SearchProfileRepository",
        "SearchProfileService",
        "SerpApiDiscoveryProvider",
        "SerpApiClient",
        "CompanyIngestionService",
        "session.add",
        "commit(",
        "rollback(",
        "flush(",
    ],
)
def test_execution_core_has_no_forbidden_dependencies(
    forbidden_dependency: str,
) -> None:
    source = inspect.getsource(profile_execution_module)

    assert forbidden_dependency.casefold() not in source.casefold()


def test_search_profile_module_does_not_import_execution_core() -> None:
    source_root = profile_execution_module.__file__
    assert source_root is not None

    search_profile_sources = (Path(source_root).parents[1] / "search_profile").glob("*.py")

    for source_path in search_profile_sources:
        assert "profile_execution" not in source_path.read_text(encoding="utf-8")


def test_execution_core_performs_no_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = make_query(1)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Execution core attempted an external network call.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)

    report, _, _ = run_with([query], [make_response(query)])

    assert report.executed_queries == 1

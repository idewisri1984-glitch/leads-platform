from collections.abc import Callable

import httpx
import pytest

from app.modules.agent.lead_acquisition import (
    CompanyPlanOutcome,
    LeadAcquisitionDependencies,
    LeadAcquisitionInput,
    LeadAcquisitionProviderStopError,
    LeadAcquisitionService,
    LeadAcquisitionStatus,
)
from app.modules.company_discovery.schemas import DiscoveryProviderDiagnostic
from app.providers.serpapi.client import SerpApiClient
from app.providers.serpapi.exceptions import (
    SerpApiRequestError,
    SerpApiRequestFailureSubtype,
)


def _search(
    client: SerpApiClient,
    *,
    query: str | None = "interior design",
    country: str | None = None,
    city: str | None = None,
    industry: str | None = None,
    limit: int = 5,
    iso_country_code: str | None = "US",
) -> object:
    return client.search_companies(
        query=query,
        country=country,
        city=city,
        industry=industry,
        limit=limit,
        iso_country_code=iso_country_code,
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> SerpApiClient:
    return SerpApiClient(
        api_key="safe-key",
        base_url="https://example.invalid/search",
        timeout_seconds=1.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _dependencies(company_plan: Callable[[int, int, str], CompanyPlanOutcome]) -> object:
    def unexpected(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    return LeadAcquisitionDependencies(
        company_plan=company_plan,
        company_apply=unexpected,
        contact_plan=unexpected,
        contact_apply=unexpected,
        company_email=unexpected,
        company_complete=unexpected,
        draft_generate=unexpected,
        export_crm=unexpected,
    )


def _input() -> LeadAcquisitionInput:
    return LeadAcquisitionInput(
        project_id=1,
        search_profile_id=1,
        limit=1,
        goal="Find a relevant design firm",
    )


def test_transport_error_has_detached_safe_diagnostic() -> None:
    secret = "api_key=hostile-secret&credential=leak"

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(secret, request=request)

    with pytest.raises(SerpApiRequestError) as captured:
        _search(_client(fail))

    error = captured.value
    assert error.diagnostic is not None
    assert error.diagnostic.subtype is SerpApiRequestFailureSubtype.TRANSPORT
    assert error.diagnostic.http_status is None
    assert error.__cause__ is None
    assert error.__context__ is None
    exposed = f"{error!s} {error!r} {error.args!r} {error.diagnostic!r}"
    assert secret not in exposed
    assert "hostile-secret" not in exposed


@pytest.mark.parametrize("status", [400, 404, 422])
def test_http_client_error_preserves_only_status(status: int) -> None:
    body = "provider-body api_key=secret"
    client = _client(lambda request: httpx.Response(status, text=body, request=request))

    with pytest.raises(SerpApiRequestError) as captured:
        _search(client)

    diagnostic = captured.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.subtype is SerpApiRequestFailureSubtype.HTTP_CLIENT
    assert diagnostic.http_status == status
    assert body not in repr(captured.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 0},
        {"query": None, "country": None, "city": None, "industry": None},
        {"iso_country_code": "invalid-country"},
    ],
)
def test_local_request_validation_is_classified(changes: dict[str, object]) -> None:
    client = _client(lambda request: httpx.Response(200, json={}, request=request))

    with pytest.raises(SerpApiRequestError) as captured:
        _search(
            client,
            query=changes.get("query", "interior design"),
            country=changes.get("country"),
            city=changes.get("city"),
            industry=changes.get("industry"),
            limit=changes.get("limit", 5),
            iso_country_code=changes.get("iso_country_code", "US"),
        )

    assert captured.value.diagnostic is not None
    assert captured.value.diagnostic.subtype is SerpApiRequestFailureSubtype.VALIDATION
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_failed_provider_invocation_is_counted() -> None:
    diagnostic = DiscoveryProviderDiagnostic(
        category="request_error",
        subtype="TRANSPORT",
    )

    def fail(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        raise LeadAcquisitionProviderStopError(
            "stop",
            discovery_call_count=1,
            discovery_run_count=1,
            diagnostic=diagnostic,
        )

    result = LeadAcquisitionService(_dependencies(fail)).acquire(_input())

    assert result.attempt_count == 1
    assert result.discovery_run_count == 1
    assert result.company_discovery_call_count == 1
    assert result.company_decision_call_count == 0
    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.budget_exhausted is False
    assert result.provider_diagnostic == diagnostic


def test_pre_provider_failure_does_not_count_provider_call() -> None:
    def fail(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        raise LeadAcquisitionProviderStopError("stop")

    result = LeadAcquisitionService(_dependencies(fail)).acquire(_input())

    assert result.attempt_count == 1
    assert result.discovery_run_count == 0
    assert result.company_discovery_call_count == 0
    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP


def test_seven_successes_then_failed_invocation_preserve_all_counts() -> None:
    calls = 0

    def plan(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        nonlocal calls
        calls += 1
        if calls == 8:
            raise LeadAcquisitionProviderStopError(
                "stop",
                discovery_call_count=1,
                discovery_run_count=1,
            )
        return CompanyPlanOutcome(
            discovery_run_id=calls,
            candidate_count=0,
            selected_candidate_id=None,
            discovery_call_count=1,
            decision_call_count=1,
        )

    result = LeadAcquisitionService(_dependencies(plan)).acquire(_input())

    assert result.attempt_count == 8
    assert result.discovery_run_count == 8
    assert result.company_discovery_call_count == 8
    assert result.company_decision_call_count == 7
    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.budget_exhausted is False
    assert result.completed_count == 0

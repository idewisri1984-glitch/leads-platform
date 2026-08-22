import json
from collections.abc import Callable

import httpx
import pytest

from app.modules.agent.execution import _CountedDiscoveryProvider
from app.modules.agent.lead_acquisition import (
    CompanyApplyOutcome,
    CompanyCompletionOutcome,
    CompanyPlanOutcome,
    DraftOutcome,
    LeadAcquisitionContactUnavailableError,
    LeadAcquisitionDependencies,
    LeadAcquisitionInput,
    LeadAcquisitionProviderStopError,
    LeadAcquisitionService,
    LeadAcquisitionStatus,
)
from app.modules.agent.provider_diagnostics import OpenAIDecisionProviderDiagnostic
from app.modules.company_discovery.provider_interfaces import DiscoveryProviderRequestError
from app.modules.company_discovery.schemas import DiscoveryProviderDiagnostic
from app.modules.company_discovery.serpapi_provider import SerpApiDiscoveryProvider
from app.modules.search_profile.schemas import SearchQuery
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


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeper: Callable[[float], None] = lambda _: None,
) -> SerpApiClient:
    return SerpApiClient(
        api_key="safe-key",
        base_url="https://example.invalid/search",
        timeout_seconds=1.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=sleeper,
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


def test_openai_decision_diagnostic_survives_lazy_factory() -> None:
    from app.modules.agent.company_plan import (
        AgentCompanyPlanDecisionError,
        AgentCompanyPlanFailureSubstage,
        _decision_call,
    )
    from app.modules.agent.execution import _LazyDecisionFactory
    from app.providers.openai_decision.exceptions import (
        OpenAIDecisionDiagnostic,
        OpenAIDecisionDiagnosticCategory,
        OpenAIDecisionRequestError,
    )

    diagnostic = OpenAIDecisionDiagnostic(
        category=OpenAIDecisionDiagnosticCategory.CONNECTION,
        exception_class="APIConnectionError",
        request_id="req_safe123",
    )

    def fail() -> object:
        raise OpenAIDecisionRequestError(diagnostic=diagnostic)

    decision_factory = _LazyDecisionFactory(lambda: fail)

    with pytest.raises(AgentCompanyPlanDecisionError) as captured:
        _decision_call(decision_factory, AgentCompanyPlanFailureSubstage.COMPANY_DECISION)

    assert captured.value.diagnostic == diagnostic


def test_openai_decision_diagnostic_reaches_provider_stop_result() -> None:
    diagnostic = OpenAIDecisionProviderDiagnostic(
        category="CONNECTION",
        exception_class="APIConnectionError",
        request_id="req_safe123",
    )

    def fail(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        raise LeadAcquisitionProviderStopError("stop", diagnostic=diagnostic)

    result = LeadAcquisitionService(_dependencies(fail)).acquire(_input())
    dumped = result.model_dump(mode="json")
    restored = OpenAIDecisionProviderDiagnostic.model_validate(dumped["provider_diagnostic"])

    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.provider_diagnostic == diagnostic
    assert restored == diagnostic
    assert dumped["provider_diagnostic"] == {
        "category": "CONNECTION",
        "exception_class": "APIConnectionError",
        "http_status": None,
        "openai_error_code": None,
        "parameter": None,
        "request_id": "req_safe123",
        "response_status": None,
        "incomplete_reason": None,
    }


def test_provider_diagnostics_validate_and_round_trip_unambiguously() -> None:
    diagnostics = (
        DiscoveryProviderDiagnostic(category="request_error", subtype="TRANSPORT"),
        OpenAIDecisionProviderDiagnostic(
            category="CONNECTION",
            exception_class="APIConnectionError",
            request_id="req_safe123",
        ),
    )

    for diagnostic in diagnostics:
        dumped = diagnostic.model_dump(mode="json")
        assert type(diagnostic).model_validate(dumped) == diagnostic


def test_lead_acquisition_result_import_is_provider_safe() -> None:
    import subprocess
    import sys

    script = """
import sys
from app.modules.agent.lead_acquisition import LeadAcquisitionResult

blocked = {
    'app.core.config.settings',
    'app.providers.openai_decision.client',
    'app.providers.serpapi.client',
}
loaded = sorted(blocked.intersection(sys.modules))
database = sys.modules.get('app.core.database')
materialized = [] if database is None else sorted(
    {'engine', 'SessionLocal'}.intersection(vars(database))
)
problems = loaded + materialized
if problems:
    raise SystemExit(','.join(problems))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_unknown_decision_exception_remains_sanitized() -> None:
    from app.modules.agent.company_plan import AgentCompanyPlanDecisionError
    from app.modules.agent.execution import _LazyDecisionFactory

    secret = "API_KEY=should-never-appear"

    def fail() -> object:
        raise RuntimeError(secret)

    with pytest.raises(AgentCompanyPlanDecisionError) as captured:
        _LazyDecisionFactory(lambda: fail)()

    assert captured.value.diagnostic is None
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def _stage6a_result_from_decision_error(error_factory: Callable[[], Exception]) -> object:
    from app.core.database.session import SessionLocal
    from app.modules.agent.lead_acquisition import LeadAcquisitionInput
    from app.modules.lead_acquisition_execution import execute_lead_acquisition
    from app.modules.project.models import Project
    from app.modules.search_profile.models import SearchProfile

    with SessionLocal() as session:
        project = Project(name="Stage 6A provider diagnostic mapping")
        session.add(project)
        session.flush()
        profile = SearchProfile(
            project_id=project.id,
            name="Provider diagnostic profile",
            product_or_service="Custom interior products",
            target_customer_types=["interior design studios"],
            countries=["United States"],
            cities=[],
            query_templates=["{target_customer_type} {country}"],
            result_limit=5,
            max_queries_per_run=1,
            total_result_ceiling=5,
            enabled=True,
        )
        session.add(profile)
        session.commit()
        project_id = project.id
        profile_id = profile.id

    class DecisionBoundary:
        def decide(self, request: object) -> object:
            raise error_factory()

    class DecisionFactory:
        def __call__(self) -> DecisionBoundary:
            return DecisionBoundary()

        def close(self) -> None:
            return None

    def search(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "title": "Atelier Example",
                        "link": "https://atelier.example",
                        "snippet": "Luxury residential and hospitality interior design studio.",
                        "position": 1,
                    }
                ]
            },
            request=request,
        )

    return execute_lead_acquisition(
        LeadAcquisitionInput(
            project_id=project_id,
            search_profile_id=profile_id,
            limit=1,
            goal="Find a relevant interior design firm",
        ),
        session_factory=SessionLocal,
        decision_factory_factory=DecisionFactory,
        serpapi_client_factory=lambda **kwargs: _client(search),
        contact_provider_factory=lambda: (_ for _ in ()).throw(AssertionError),
        company_enrichment_provider_factory=lambda: (_ for _ in ()).throw(AssertionError),
        email_generator_factory=lambda: (_ for _ in ()).throw(AssertionError),
    )


def test_openai_decision_diagnostic_survives_stage6a_execution_mapping() -> None:
    from app.providers.openai_decision.exceptions import (
        OpenAIDecisionDiagnostic,
        OpenAIDecisionDiagnosticCategory,
        OpenAIDecisionRequestError,
    )

    source_diagnostic = OpenAIDecisionDiagnostic(
        category=OpenAIDecisionDiagnosticCategory.CONNECTION,
        exception_class="APIConnectionError",
        request_id="req_safe123",
    )
    result = _stage6a_result_from_decision_error(
        lambda: OpenAIDecisionRequestError(diagnostic=source_diagnostic)
    )
    dumped = result.model_dump(mode="json")
    serialized = json.dumps(dumped)

    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert isinstance(result.provider_diagnostic, OpenAIDecisionProviderDiagnostic)
    assert dumped["provider_diagnostic"]["category"] == "CONNECTION"
    assert dumped["provider_diagnostic"]["exception_class"] == "APIConnectionError"
    assert dumped["provider_diagnostic"]["request_id"] == "req_safe123"
    for forbidden in ("traceback", "__cause__", "__context__", "authorization", "api_key"):
        assert forbidden not in serialized.casefold()


def test_unknown_decision_exception_stays_sanitized_through_stage6a_execution_mapping() -> None:
    secret = "API_KEY=should-never-appear"
    result = _stage6a_result_from_decision_error(lambda: RuntimeError(secret))
    serialized = json.dumps(result.model_dump(mode="json"))

    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.provider_diagnostic is None
    assert secret not in serialized
    assert "traceback" not in serialized.casefold()
    assert "__cause__" not in serialized
    assert "__context__" not in serialized


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


def test_sleeper_failure_is_sanitized_at_counted_provider_boundary() -> None:
    transport_secret = "SECRET_TRANSPORT_BOUNDARY"
    sleeper_secret = "SECRET_SLEEPER_BOUNDARY"
    sleeper_calls: list[float] = []

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(transport_secret, request=request)

    def failing_sleeper(delay: float) -> None:
        sleeper_calls.append(delay)
        raise RuntimeError(sleeper_secret)

    counted = _CountedDiscoveryProvider(
        SerpApiDiscoveryProvider(_client(fail, sleeper=failing_sleeper))
    )
    query = SearchQuery(
        text="interior design",
        profile_id=1,
        profile_name="Profile",
        country="United States",
        city=None,
        source_template="{target_customer_type}",
        country_code="US",
        limit=5,
    )

    with pytest.raises(DiscoveryProviderRequestError) as captured:
        counted.search(query)

    error = captured.value
    exposed = f"{error!s} {error!r} {error.args!r} {error.diagnostic!r}"
    assert error.__cause__ is error.__context__ is None
    assert transport_secret not in exposed
    assert sleeper_secret not in exposed
    assert counted.snapshot_call_count() == 1
    assert sleeper_calls == [0.5]


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


def test_http_status_error_is_status_based_not_transport() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("hostile-secret", request=request, response=response)

    with pytest.raises(SerpApiRequestError) as captured:
        _search(_client(fail))

    diagnostic = captured.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.subtype is SerpApiRequestFailureSubtype.HTTP_CLIENT
    assert diagnostic.http_status == 400
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "hostile-secret" not in repr(captured.value)


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


def test_production_counter_does_not_count_local_validation() -> None:
    http_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json={"organic_results": []}, request=request)

    counted = _CountedDiscoveryProvider(SerpApiDiscoveryProvider(_client(handler)))
    query = SearchQuery.model_construct(
        text="interior design",
        profile_id=1,
        profile_name="Profile",
        country="United States",
        city=None,
        source_template="{target_customer_type}",
        country_code="ZZ",
        limit=5,
    )

    with pytest.raises(DiscoveryProviderRequestError):
        counted.search(query)

    assert counted.snapshot_call_count() == 0
    assert http_calls == 0


def test_seven_successes_then_failed_invocation_preserve_all_counts() -> None:
    http_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        if http_calls in {8, 9}:
            raise httpx.ReadError("controlled transport failure", request=request)
        return httpx.Response(200, json={"organic_results": []}, request=request)

    counted = _CountedDiscoveryProvider(SerpApiDiscoveryProvider(_client(handler)))
    query = SearchQuery(
        text="interior design",
        profile_id=1,
        profile_name="Profile",
        country="United States",
        city=None,
        source_template="{target_customer_type}",
        country_code="US",
        limit=5,
    )

    def plan(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        before_calls = counted.snapshot_call_count()
        try:
            counted.search(query)
        except DiscoveryProviderRequestError:
            raise LeadAcquisitionProviderStopError(
                "stop",
                discovery_call_count=counted.snapshot_call_count() - before_calls,
                discovery_run_count=1,
            ) from None
        calls = counted.snapshot_call_count()
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
    assert result.company_discovery_call_count == 9
    assert result.company_decision_call_count == 7
    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.budget_exhausted is False
    assert result.completed_count == 0
    assert http_calls == 9


def test_stage6_transport_retry_success_preserves_one_logical_attempt() -> None:
    http_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        if http_calls == 1:
            raise httpx.ConnectError("transient", request=request)
        return httpx.Response(200, json={"organic_results": []}, request=request)

    counted = _CountedDiscoveryProvider(SerpApiDiscoveryProvider(_client(handler)))
    query = SearchQuery(
        text="interior design",
        profile_id=1,
        profile_name="Profile",
        country="United States",
        city=None,
        source_template="{target_customer_type}",
        country_code="US",
        limit=5,
    )

    def plan(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        before = counted.snapshot_call_count()
        counted.search(query)
        return CompanyPlanOutcome(1, 1, 1, counted.snapshot_call_count() - before, 1)

    dependencies = LeadAcquisitionDependencies(
        company_plan=plan,
        company_apply=lambda *args: CompanyApplyOutcome(1, True, False),
        contact_plan=lambda *args: (_ for _ in ()).throw(
            LeadAcquisitionContactUnavailableError("unavailable", discovery_call_count=0)
        ),
        contact_apply=lambda *args: (_ for _ in ()).throw(AssertionError(args)),
        company_email=lambda company_id: "company@example.com",
        company_complete=lambda *args: CompanyCompletionOutcome(1, 1, True, False, True, False),
        draft_generate=lambda *args: DraftOutcome(
            1, None, 1, "company@example.com", "DRAFT", True, False
        ),
        export_crm=lambda *args: (_ for _ in ()).throw(AssertionError(args)),
    )

    result = LeadAcquisitionService(dependencies).acquire(_input())

    assert result.attempt_count == 1
    assert result.discovery_run_count == 1
    assert result.company_discovery_call_count == 2
    assert result.status is LeadAcquisitionStatus.COMPLETE
    assert http_calls == 2


def test_stage6_double_transport_stops_after_one_logical_attempt() -> None:
    http_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        raise httpx.ReadError("transport", request=request)

    counted = _CountedDiscoveryProvider(SerpApiDiscoveryProvider(_client(handler)))
    query = SearchQuery(
        text="interior design",
        profile_id=1,
        profile_name="Profile",
        country="United States",
        city=None,
        source_template="{target_customer_type}",
        country_code="US",
        limit=5,
    )

    def plan(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        before = counted.snapshot_call_count()
        try:
            counted.search(query)
        except DiscoveryProviderRequestError:
            raise LeadAcquisitionProviderStopError(
                "stop",
                discovery_call_count=counted.snapshot_call_count() - before,
                discovery_run_count=1,
                diagnostic=DiscoveryProviderDiagnostic(
                    category="request_error", subtype="TRANSPORT"
                ),
            ) from None
        raise AssertionError("transport unexpectedly recovered")

    result = LeadAcquisitionService(_dependencies(plan)).acquire(_input())

    assert result.attempt_count == 1
    assert result.discovery_run_count == 1
    assert result.company_discovery_call_count == 2
    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.provider_diagnostic is not None
    assert result.provider_diagnostic.subtype == "TRANSPORT"
    assert http_calls == 2


@pytest.mark.parametrize("failed_call_count", [0, 1])
def test_contact_failure_telemetry_preserves_company_email_fallback(
    failed_call_count: int,
) -> None:
    def company_plan(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        return CompanyPlanOutcome(1, 1, 1, 1, 1)

    def contact_plan(project_id: int, company_id: int, goal: str) -> object:
        raise LeadAcquisitionContactUnavailableError(
            "contact unavailable",
            discovery_call_count=failed_call_count,
        )

    dependencies = LeadAcquisitionDependencies(
        company_plan=company_plan,
        company_apply=lambda project_id, run_id, candidate_id: CompanyApplyOutcome(
            company_id=1,
            created=True,
            reused=False,
        ),
        contact_plan=contact_plan,
        contact_apply=lambda *args: (_ for _ in ()).throw(AssertionError(args)),
        company_email=lambda company_id: "company@example.com",
        company_complete=lambda project_id, company_id, title: CompanyCompletionOutcome(
            lead_id=1,
            task_id=1,
            lead_created=True,
            lead_reused=False,
            task_created=True,
            task_reused=False,
        ),
        draft_generate=lambda *args: DraftOutcome(
            draft_id=1,
            contact_id=None,
            lead_id=1,
            recipient_email="company@example.com",
            status="DRAFT",
            created=True,
            reused=False,
        ),
        export_crm=lambda *args: (_ for _ in ()).throw(AssertionError(args)),
    )

    result = LeadAcquisitionService(dependencies).acquire(_input())

    assert result.status is LeadAcquisitionStatus.COMPLETE
    assert result.contact_discovery_call_count == failed_call_count
    assert result.company_scoped_count == 1

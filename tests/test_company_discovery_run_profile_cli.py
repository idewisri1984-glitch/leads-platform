from typing import Any

import pytest
from typer.testing import CliRunner

from app.cli import company_discovery as cli
from app.modules.company_discovery import (
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryExecutionError,
    SearchProfileDiscoveryPersistenceError,
    SearchProfileDiscoveryPersistResult,
    SearchProfileDiscoveryProviderError,
)
from app.modules.company_discovery.schemas import ProviderErrorCode, StopReason
from app.modules.company_import.schemas import CompanyIngestionResult
from app.modules.search_profile import (
    SearchProfileQueryGenerator,
    SearchProfileRead,
    SearchProfileRunOptions,
)

runner = CliRunner()


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


class FakeSessionLocal:
    closed = False
    calls = 0

    def __enter__(self) -> object:
        type(self).calls += 1
        type(self).closed = False
        return object()

    def __exit__(self, *args: object) -> None:
        type(self).closed = True


class FakeSearchProfileService:
    profile: SearchProfileRead | None = make_profile()

    def __init__(self, repository: object) -> None:
        pass

    def get(self, profile_id: int) -> SearchProfileRead | None:
        assert profile_id == 7
        return type(self).profile


class FakeClient:
    def __init__(self, **kwargs: object) -> None:
        pass


def reset_fakes() -> None:
    FakeSessionLocal.calls = 0
    FakeSessionLocal.closed = False
    FakeSearchProfileService.profile = make_profile()


def test_run_profile_dry_run_executes_after_database_session_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    calls: list[tuple[SearchProfileRead, Any]] = []

    class FakeExecutionService:
        def __init__(self, generator: object) -> None:
            pass

        def run_dry(self, profile: SearchProfileRead, provider: object, options: Any) -> Any:
            assert FakeSessionLocal.closed
            calls.append((profile, options))
            query = SearchProfileQueryGenerator().generate_preview(profile, options).queries[0]
            return SearchProfileDiscoveryDryRunResult(
                profile_id=profile.id,
                profile_name=profile.name,
                provider="serpapi",
                query_count=1,
                estimated_provider_requests=1,
                executed_queries=1,
                total_provider_results=0,
                total_adapted_items=0,
                total_adapter_errors=0,
                total_provider_errors=0,
                total_result_ceiling=10,
                stopped_early=False,
                query_results=[
                    {
                        "query": query,
                        "provider": "serpapi",
                        "provider_result_count": 0,
                        "adapted_item_count": 0,
                        "adapter_error_count": 0,
                    }
                ],
            )

    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", FakeSearchProfileService)
    monkeypatch.setattr(cli, "SerpApiClient", FakeClient)
    monkeypatch.setattr(cli, "SearchProfileDiscoveryService", FakeExecutionService)
    monkeypatch.setattr(
        cli,
        "SearchProfileDiscoveryPersistenceService",
        lambda *args: pytest.fail("persistence service created"),
    )

    result = runner.invoke(
        cli.app,
        [
            "run-profile",
            "--profile-id",
            "7",
            "--provider",
            "serpapi",
            "--dry-run",
            "--max-queries",
            "1",
            "--total-result-ceiling",
            "10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert FakeSessionLocal.calls == 1
    assert calls[0][1].max_queries == 1
    assert "Companies persisted: 0" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--profile-id", "7", "--provider", "serpapi"],
            "Choose exactly one mode: --dry-run or --persist.",
        ),
        (
            [
                "--profile-id",
                "7",
                "--provider",
                "serpapi",
                "--dry-run",
                "--persist",
                "--yes",
            ],
            "Choose exactly one mode: --dry-run or --persist.",
        ),
        (
            ["--profile-id", "7", "--provider", "serpapi", "--dry-run", "--yes"],
            "--yes is valid only with --persist.",
        ),
        (
            ["--profile-id", "7", "--provider", "serpapi", "--persist"],
            "Persistence requires --yes.",
        ),
        (
            ["--profile-id", "7", "--provider", "other", "--dry-run"],
            "supports only serpapi",
        ),
        (
            [
                "--profile-id",
                "7",
                "--provider",
                "serpapi",
                "--dry-run",
                "--max-queries",
                "0",
            ],
            "Invalid search profile run options",
        ),
    ],
)
def test_run_profile_rejects_invalid_input_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(cli, "SessionLocal", lambda: pytest.fail("database opened"))
    monkeypatch.setattr(cli, "SerpApiClient", lambda **kwargs: pytest.fail("client created"))
    monkeypatch.setattr(
        cli,
        "SearchProfileService",
        lambda *args, **kwargs: pytest.fail("profile service created"),
    )
    monkeypatch.setattr(
        cli,
        "SearchProfileDiscoveryService",
        lambda *args, **kwargs: pytest.fail("discovery service created"),
    )
    monkeypatch.setattr(
        cli,
        "SearchProfileDiscoveryPersistenceService",
        lambda *args, **kwargs: pytest.fail("persistence service created"),
    )

    result = runner.invoke(cli.app, ["run-profile", *arguments])

    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


def test_run_profile_missing_profile_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    FakeSearchProfileService.profile = None
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", FakeSearchProfileService)
    monkeypatch.setattr(cli, "SerpApiClient", lambda **kwargs: pytest.fail("client created"))

    result = runner.invoke(
        cli.app,
        ["run-profile", "--profile-id", "7", "--provider", "serpapi", "--dry-run"],
    )

    assert result.exit_code == 1
    assert "Search profile 7 not found." in result.output
    assert "Traceback" not in result.output


def test_disabled_profile_execution_error_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()

    class DisabledExecutionService:
        def __init__(self, generator: object) -> None:
            pass

        def run_dry(self, *args: object) -> None:
            raise SearchProfileDiscoveryExecutionError("Search profile is disabled.")

    FakeSearchProfileService.profile = make_profile(enabled=False)
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", FakeSearchProfileService)
    monkeypatch.setattr(cli, "SerpApiClient", FakeClient)
    monkeypatch.setattr(cli, "SearchProfileDiscoveryService", DisabledExecutionService)

    result = runner.invoke(
        cli.app,
        ["run-profile", "--profile-id", "7", "--provider", "serpapi", "--dry-run"],
    )

    assert result.exit_code == 1
    assert "Search profile is disabled." in result.output
    assert "Traceback" not in result.output


class LifecycleSession:
    def __init__(self, number: int) -> None:
        self.number = number
        self.closed = False

    def add(self, *args: object) -> None:
        raise AssertionError("CLI must not call session.add().")

    def flush(self, *args: object) -> None:
        raise AssertionError("CLI must not call session.flush().")

    def commit(self, *args: object) -> None:
        raise AssertionError("CLI must not call session.commit().")

    def rollback(self, *args: object) -> None:
        raise AssertionError("CLI must not call session.rollback().")


class LifecycleSessionLocal:
    sessions: list[LifecycleSession] = []

    def __init__(self) -> None:
        self.session = LifecycleSession(len(type(self).sessions) + 1)
        type(self).sessions.append(self.session)

    def __enter__(self) -> LifecycleSession:
        return self.session

    def __exit__(self, *args: object) -> None:
        self.session.closed = True


def make_persist_report(
    *,
    ingestion_result: CompanyIngestionResult | None = None,
    stopped_early: bool = False,
    stop_reason: StopReason | None = None,
    provider_error: SearchProfileDiscoveryProviderError | None = None,
) -> SearchProfileDiscoveryPersistResult:
    profile = make_profile()
    query = (
        SearchProfileQueryGenerator()
        .generate_preview(
            profile,
            SearchProfileRunOptions(max_queries=1, result_limit_per_query=3),
        )
        .queries[0]
    )
    adapted_count = ingestion_result.total_rows if ingestion_result is not None else 0
    return SearchProfileDiscoveryPersistResult(
        profile_id=profile.id,
        profile_name=profile.name,
        provider="serpapi",
        query_count=1,
        estimated_provider_requests=1,
        executed_queries=1,
        total_provider_results=adapted_count,
        total_adapted_items=adapted_count,
        total_adapter_errors=0,
        total_provider_errors=provider_error is not None,
        total_result_ceiling=3,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
        query_results=[
            {
                "query": query,
                "provider": "serpapi",
                "provider_result_count": adapted_count,
                "adapted_item_count": adapted_count,
                "adapter_error_count": 0,
                "provider_error": provider_error,
                "items": [
                    {"source_row_number": number, "name": f"Company {number}"}
                    for number in range(1, adapted_count + 1)
                ],
            }
        ],
        ingestion_attempted=ingestion_result is not None,
        total_items_submitted_to_ingestion=adapted_count,
        ingestion_result=ingestion_result,
    )


def successful_ingestion(*, rolled_back: bool = False) -> CompanyIngestionResult:
    return CompanyIngestionResult(
        total_rows=2,
        imported=0 if rolled_back else 1,
        skipped_duplicates=1,
        failed=1 if rolled_back else 0,
        created_company_ids=[] if rolled_back else [101],
        duplicates=[
            {
                "source_row_number": 2,
                "existing_company_id": 55,
                "matched_by": "website_hostname",
                "matched_value": "duplicate.example",
            }
        ],
        errors=(
            [
                {
                    "source_row_number": None,
                    "code": "persistence_error",
                    "message": "Company ingestion was rolled back.",
                }
            ]
            if rolled_back
            else []
        ),
        rolled_back=rolled_back,
    )


def run_persist_with_report(
    monkeypatch: pytest.MonkeyPatch,
    report: SearchProfileDiscoveryPersistResult,
) -> tuple[Any, list[tuple[SearchProfileRead, object, Any]]]:
    reset_fakes()
    LifecycleSessionLocal.sessions = []
    calls: list[tuple[SearchProfileRead, object, Any]] = []

    class ForbiddenDryRunService:
        def __init__(self, generator: object) -> None:
            pass

        def run_dry(self, *args: object) -> None:
            raise AssertionError("CLI must not call run_dry directly in persist mode.")

    class FakePersistenceService:
        def __init__(self, discovery_service: object) -> None:
            assert isinstance(discovery_service, ForbiddenDryRunService)

        def run_persist(
            self,
            *,
            session: LifecycleSession,
            profile: SearchProfileRead,
            provider: object,
            options: Any,
        ) -> SearchProfileDiscoveryPersistResult:
            assert LifecycleSessionLocal.sessions[0].closed
            assert session is LifecycleSessionLocal.sessions[1]
            assert not session.closed
            calls.append((profile, provider, options))
            return report

    monkeypatch.setattr(cli, "SessionLocal", LifecycleSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", FakeSearchProfileService)
    monkeypatch.setattr(cli, "SerpApiClient", FakeClient)
    monkeypatch.setattr(cli, "SearchProfileDiscoveryService", ForbiddenDryRunService)
    monkeypatch.setattr(
        cli,
        "SearchProfileDiscoveryPersistenceService",
        FakePersistenceService,
    )

    result = runner.invoke(
        cli.app,
        [
            "run-profile",
            "--profile-id",
            "7",
            "--provider",
            "serpapi",
            "--persist",
            "--yes",
            "--max-queries",
            "1",
            "--result-limit-per-query",
            "3",
            "--total-result-ceiling",
            "3",
        ],
    )
    return result, calls


def test_persist_happy_path_uses_separate_closed_sessions_and_prints_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = make_persist_report(ingestion_result=successful_ingestion())

    result, calls = run_persist_with_report(monkeypatch, report)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][0] == make_profile()
    assert calls[0][2].max_queries == 1
    assert len(LifecycleSessionLocal.sessions) == 2
    assert all(session.closed for session in LifecycleSessionLocal.sessions)
    for expected in (
        "Dry run: False",
        "Persistence requested: True",
        "Provider: serpapi",
        "Query Count: 1",
        "Executed Queries: 1",
        "Items submitted to ingestion: 2",
        "Ingestion attempted: True",
        "Imported: 1",
        "Skipped duplicates: 1",
        "Failed: 0",
        "Rolled back: False",
        "Created company IDs: [101]",
        "Companies persisted: 1",
    ):
        assert expected in result.output
    assert "Traceback" not in result.output


def test_persist_rolled_back_exits_one_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = run_persist_with_report(
        monkeypatch,
        make_persist_report(ingestion_result=successful_ingestion(rolled_back=True)),
    )

    assert result.exit_code == 1
    assert "Rolled back: True" in result.output
    assert all(session.closed for session in LifecycleSessionLocal.sessions)


@pytest.mark.parametrize(
    "stop_reason", ["configuration_error", "rate_limit_error", "provider_error"]
)
def test_terminal_provider_stop_exits_one_after_safe_report(
    monkeypatch: pytest.MonkeyPatch,
    stop_reason: StopReason,
) -> None:
    report = make_persist_report(
        ingestion_result=successful_ingestion(),
        stopped_early=True,
        stop_reason=stop_reason,
        provider_error=SearchProfileDiscoveryProviderError(
            code=stop_reason,
            message="Safe provider failure.",
        ),
    )

    result, _ = run_persist_with_report(monkeypatch, report)

    assert result.exit_code == 1
    assert f"Stop Reason: {stop_reason}" in result.output
    assert "Companies persisted: 1" in result.output


@pytest.mark.parametrize("error_code", ["request_error", "response_error"])
def test_nonterminal_provider_errors_can_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
    error_code: ProviderErrorCode,
) -> None:
    report = make_persist_report(
        ingestion_result=successful_ingestion(),
        provider_error=SearchProfileDiscoveryProviderError(
            code=error_code,
            message="Safe query failure.",
        ),
    )

    result, _ = run_persist_with_report(monkeypatch, report)

    assert result.exit_code == 0
    assert "Total Provider Errors: 1" in result.output
    assert "Provider Error: Safe query failure." in result.output


def test_zero_item_persist_is_successful_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = run_persist_with_report(monkeypatch, make_persist_report())

    assert result.exit_code == 0
    assert "Ingestion attempted: False" in result.output
    assert "Imported: 0" in result.output
    assert "Companies persisted: 0" in result.output


def test_controlled_persistence_error_is_safe_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    LifecycleSessionLocal.sessions = []
    raw_marker = "raw SQL api-key-marker sqlite-private-db"

    class FakePersistenceService:
        def __init__(self, discovery_service: object) -> None:
            pass

        def run_persist(self, **kwargs: object) -> None:
            try:
                raise RuntimeError(raw_marker)
            except RuntimeError:
                raise SearchProfileDiscoveryPersistenceError("Company ingestion failed.") from None

    monkeypatch.setattr(cli, "SessionLocal", LifecycleSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", FakeSearchProfileService)
    monkeypatch.setattr(cli, "SerpApiClient", FakeClient)
    monkeypatch.setattr(cli, "SearchProfileDiscoveryPersistenceService", FakePersistenceService)

    result = runner.invoke(
        cli.app,
        ["run-profile", "--profile-id", "7", "--provider", "serpapi", "--persist", "--yes"],
    )

    assert result.exit_code == 1
    assert "Company ingestion failed." in result.output
    assert raw_marker not in result.output
    assert "Traceback" not in result.output
    assert all(session.closed for session in LifecycleSessionLocal.sessions)


def test_disabled_profile_in_persist_mode_is_controlled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    LifecycleSessionLocal.sessions = []
    FakeSearchProfileService.profile = make_profile(enabled=False)

    class DisabledPersistenceService:
        def __init__(self, discovery_service: object) -> None:
            pass

        def run_persist(self, **kwargs: object) -> None:
            raise SearchProfileDiscoveryExecutionError("Search profile is disabled.")

    monkeypatch.setattr(cli, "SessionLocal", LifecycleSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", FakeSearchProfileService)
    monkeypatch.setattr(cli, "SerpApiClient", FakeClient)
    monkeypatch.setattr(
        cli,
        "SearchProfileDiscoveryPersistenceService",
        DisabledPersistenceService,
    )

    result = runner.invoke(
        cli.app,
        ["run-profile", "--profile-id", "7", "--provider", "serpapi", "--persist", "--yes"],
    )

    assert result.exit_code == 1
    assert "Search profile is disabled." in result.output
    assert "Traceback" not in result.output
    assert all(session.closed for session in LifecycleSessionLocal.sessions)

from typing import Any

import pytest
from typer.testing import CliRunner

from app.cli import company_discovery as cli
from app.modules.company_discovery import (
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryExecutionError,
)
from app.modules.search_profile import SearchProfileQueryGenerator, SearchProfileRead

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

    def __enter__(self) -> object:
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


def test_run_profile_dry_run_executes_after_database_session_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert calls[0][1].max_queries == 1
    assert "Companies persisted: 0" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--profile-id", "7", "--provider", "serpapi"], "--dry-run is required"),
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

    result = runner.invoke(cli.app, ["run-profile", *arguments])

    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


def test_run_profile_missing_profile_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
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
    FakeSearchProfileService.profile = make_profile()


def test_disabled_profile_execution_error_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
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
    FakeSearchProfileService.profile = make_profile()

from typing import Any

import pytest
from typer.testing import CliRunner

from app.cli import company_discovery as company_discovery_cli
from app.modules.company_discovery import (
    CompanyDiscoveryPersistenceResult,
    CompanyDiscoveryRequest,
    CompanyDiscoveryResult,
)
from app.modules.company_import.schemas import CompanyIngestionError, CompanyIngestionItem

runner = CliRunner()


class FakeSerpApiClient:
    calls = 0

    def __init__(self, **kwargs: object) -> None:
        type(self).calls += 1
        self.kwargs = kwargs


class FailingSerpApiClient:
    def __init__(self, **kwargs: object) -> None:
        raise AssertionError("SerpAPI client must not be constructed.")


class FailingSessionLocal:
    def __init__(self) -> None:
        raise AssertionError("SessionLocal must not be opened.")


class FakeSession:
    pass


class FakeSessionLocal:
    calls = 0

    def __init__(self) -> None:
        type(self).calls += 1
        self.session = FakeSession()

    def __enter__(self) -> FakeSession:
        return self.session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


def reset_fakes() -> None:
    FakeSerpApiClient.calls = 0
    FakeSessionLocal.calls = 0


def test_dry_run_does_not_open_session(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    discovery_calls = 0

    class FakeCompanyDiscoveryService:
        def __init__(self, client: FakeSerpApiClient) -> None:
            self.client = client

        def discover_from_serpapi(
            self,
            request: CompanyDiscoveryRequest,
        ) -> CompanyDiscoveryResult:
            nonlocal discovery_calls
            discovery_calls += 1
            assert request.query == "software"
            return CompanyDiscoveryResult(
                query="software",
                total_results=1,
                items=[
                    CompanyIngestionItem(
                        source_row_number=1,
                        name="Acme",
                        website="https://acme.example",
                    )
                ],
                errors=[],
            )

    monkeypatch.setattr(company_discovery_cli, "SerpApiClient", FakeSerpApiClient)
    monkeypatch.setattr(company_discovery_cli, "SessionLocal", FailingSessionLocal)
    monkeypatch.setattr(
        company_discovery_cli,
        "CompanyDiscoveryService",
        FakeCompanyDiscoveryService,
    )

    result = runner.invoke(company_discovery_cli.app, ["--query", "software"])

    assert result.exit_code == 0
    assert FakeSerpApiClient.calls == 1
    assert discovery_calls == 1
    assert "Discovered companies" in result.output


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--query", "software", "--yes"], "--yes is valid only with --persist."),
        (
            ["--query", "software", "--persist", "--project-id", "42"],
            "Persistence requires --yes.",
        ),
    ],
)
def test_confirmation_errors_exit_before_dependency_construction(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(company_discovery_cli, "SerpApiClient", FailingSerpApiClient)
    monkeypatch.setattr(company_discovery_cli, "SessionLocal", FailingSessionLocal)
    monkeypatch.setattr(
        company_discovery_cli,
        "CompanyDiscoveryService",
        lambda *args, **kwargs: pytest.fail("service constructed"),
    )

    result = runner.invoke(
        company_discovery_cli.app,
        arguments,
    )

    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


def test_persist_without_project_id_exits_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(company_discovery_cli, "SerpApiClient", FailingSerpApiClient)
    monkeypatch.setattr(company_discovery_cli, "SessionLocal", FailingSessionLocal)
    monkeypatch.setattr(
        company_discovery_cli,
        "CompanyDiscoveryService",
        lambda *args, **kwargs: pytest.fail("service constructed"),
    )

    result = runner.invoke(
        company_discovery_cli.app,
        ["--query", "software", "--persist", "--yes"],
    )

    assert result.exit_code == 1
    assert "--project-id is required when --persist is used." in result.output
    assert "Traceback" not in result.output


def test_persist_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    persist_calls: list[dict[str, Any]] = []

    class FakeCompanyDiscoveryService:
        def __init__(self, client: FakeSerpApiClient) -> None:
            self.client = client

        def discover_and_ingest_from_serpapi(
            self,
            *,
            session: FakeSession,
            project_id: int,
            request: CompanyDiscoveryRequest,
        ) -> CompanyDiscoveryPersistenceResult:
            persist_calls.append(
                {
                    "session": session,
                    "project_id": project_id,
                    "request": request,
                }
            )
            return CompanyDiscoveryPersistenceResult(
                query="software",
                discovered=2,
                imported=2,
                skipped_duplicates=0,
                failed=0,
                created_company_ids=[10, 11],
                errors=[],
                rolled_back=False,
            )

    monkeypatch.setattr(company_discovery_cli, "SerpApiClient", FakeSerpApiClient)
    monkeypatch.setattr(company_discovery_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(
        company_discovery_cli,
        "CompanyDiscoveryService",
        FakeCompanyDiscoveryService,
    )

    result = runner.invoke(
        company_discovery_cli.app,
        ["--query", "software", "--persist", "--yes", "--project-id", "42"],
    )

    assert result.exit_code == 0
    assert FakeSessionLocal.calls == 1
    assert len(persist_calls) == 1
    assert persist_calls[0]["project_id"] == 42
    request = persist_calls[0]["request"]
    assert isinstance(request, CompanyDiscoveryRequest)
    assert request.query == "software"
    assert "Discovered: 2" in result.output
    assert "Imported: 2" in result.output
    assert "Created company IDs: 10, 11" in result.output
    assert "Rolled back: False" in result.output


def test_persist_rolled_back_result_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()

    class FakeCompanyDiscoveryService:
        def __init__(self, client: FakeSerpApiClient) -> None:
            self.client = client

        def discover_and_ingest_from_serpapi(
            self,
            *,
            session: FakeSession,
            project_id: int,
            request: CompanyDiscoveryRequest,
        ) -> CompanyDiscoveryPersistenceResult:
            return CompanyDiscoveryPersistenceResult(
                query="software",
                discovered=1,
                imported=0,
                skipped_duplicates=0,
                failed=1,
                created_company_ids=[],
                errors=[
                    CompanyIngestionError(
                        source_row_number=None,
                        code="persistence_error",
                        message="Company ingestion was rolled back due to a persistence error.",
                    )
                ],
                rolled_back=True,
            )

    monkeypatch.setattr(company_discovery_cli, "SerpApiClient", FakeSerpApiClient)
    monkeypatch.setattr(company_discovery_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(
        company_discovery_cli,
        "CompanyDiscoveryService",
        FakeCompanyDiscoveryService,
    )

    result = runner.invoke(
        company_discovery_cli.app,
        ["--query", "software", "--persist", "--yes", "--project-id", "42"],
    )

    assert result.exit_code == 1
    assert "Rolled back: True" in result.output
    assert "Persistence failed; transaction was rolled back." in result.output
    assert "Code: persistence_error" in result.output
    assert "api_key" not in result.output.casefold()

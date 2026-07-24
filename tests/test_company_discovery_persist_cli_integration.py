from collections import deque
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import company_discovery as cli
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_discovery import (
    DiscoveryProviderRateLimitError,
    DiscoveryProviderRequestError,
    DiscoveryProviderResponse,
    DiscoveryProviderResult,
)
from app.modules.project.models import Project
from app.modules.search_profile.models import SearchProfile

runner = CliRunner()


class NoNetworkClient:
    def __init__(self, **kwargs: object) -> None:
        pass

    def search_companies(self, **kwargs: object) -> None:
        raise AssertionError("Real SerpAPI calls are forbidden in integration tests.")


class FakeProvider:
    provider_name = "serpapi"

    def __init__(self, outcomes: list[DiscoveryProviderResponse | Exception]) -> None:
        self.outcomes = deque(outcomes)
        self.calls = 0

    def search(self, query: object) -> DiscoveryProviderResponse:
        self.calls += 1
        outcome = self.outcomes.popleft()

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def provider_response(*companies: tuple[str, str]) -> DiscoveryProviderResponse:
    return DiscoveryProviderResponse(
        provider="serpapi",
        query="safe deterministic query",
        results=[
            DiscoveryProviderResult(title=name, link=website, position=position)
            for position, (name, website) in enumerate(companies, start=1)
        ],
    )


def create_project_and_profile(*, query_count: int = 1) -> tuple[int, int]:
    with SessionLocal() as session:
        project = Project(name="Integration Project")
        session.add(project)
        session.flush()
        profile = SearchProfile(
            project_id=project.id,
            name="Integration SearchProfile",
            product_or_service="business software",
            target_customer_types=[f"customer type {index}" for index in range(query_count)],
            target_industries=[],
            positive_keywords=[],
            negative_keywords=[],
            countries=["USA"],
            cities=[],
            languages=[],
            query_templates=["{target_customer_type} {country}"],
            result_limit=3,
            max_queries_per_run=query_count,
            total_result_ceiling=6,
            enabled=True,
        )
        session.add(profile)
        session.commit()
        return project.id, profile.id


def install_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> None:
    monkeypatch.setattr(cli, "SerpApiClient", NoNetworkClient)
    monkeypatch.setattr(cli, "SerpApiDiscoveryProvider", lambda client: provider)


def invoke_profile(profile_id: int, *, persist: bool = True, max_queries: int = 1) -> Any:
    mode = "--persist" if persist else "--dry-run"
    confirmation = ["--yes"] if persist else []
    return runner.invoke(
        cli.app,
        [
            "run-profile",
            "--profile-id",
            str(profile_id),
            "--provider",
            "serpapi",
            mode,
            *confirmation,
            "--max-queries",
            str(max_queries),
            "--result-limit-per-query",
            "3",
            "--total-result-ceiling",
            "6",
        ],
    )


def companies_for_project(project_id: int) -> list[Company]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Company).where(Company.project_id == project_id).order_by(Company.id)
            )
        )


def assert_safe_output(output: str) -> None:
    assert "Traceback" not in output
    assert "SERPAPI_API_KEY" not in output
    assert "Settings(" not in output
    assert "sqlite:///" not in output
    assert "organic_results" not in output
    assert '"results"' not in output


def test_successful_persist_creates_companies(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, profile_id = create_project_and_profile()
    provider = FakeProvider(
        [
            provider_response(
                ("Alpha Design", "https://alpha.example.com/about"),
                ("Beta Design", "https://beta.example.com/"),
            )
        ]
    )
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id)

    assert result.exit_code == 0, result.output
    for expected in [
        "Dry run: False",
        "Persistence requested: True",
        "Ingestion attempted: True",
        "Imported: 2",
        "Skipped duplicates: 0",
        "Failed: 0",
        "Rolled back: False",
        "Companies persisted: 2",
    ]:
        assert expected in result.output
    companies = companies_for_project(project_id)
    assert [(company.name, company.website) for company in companies] == [
        ("Alpha Design", "https://alpha.example.com/about"),
        ("Beta Design", "https://beta.example.com/"),
    ]
    assert all(company.project_id == project_id for company in companies)
    assert provider.calls == 1
    assert_safe_output(result.output)


def test_rerunning_persist_skips_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, profile_id = create_project_and_profile()
    response = provider_response(
        ("Alpha Design", "https://alpha.example.com/first"),
        ("Beta Design", "https://beta.example.com/"),
    )
    provider = FakeProvider([response, response])
    install_provider(monkeypatch, provider)

    first = invoke_profile(profile_id)
    second = invoke_profile(profile_id)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "Imported: 0" in second.output
    assert "Skipped duplicates: 2" in second.output
    assert "Failed: 0" in second.output
    assert "Rolled back: False" in second.output
    assert "Companies persisted: 0" in second.output
    assert len(companies_for_project(project_id)) == 2
    assert provider.calls == 2


def test_duplicate_rows_in_one_response_are_deduped_by_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, profile_id = create_project_and_profile()
    provider = FakeProvider(
        [
            provider_response(
                ("First Name", "https://duplicate.example.com/one"),
                ("Second Name", "https://duplicate.example.com/two"),
            )
        ]
    )
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id)

    assert result.exit_code == 0, result.output
    assert "Total Adapted Items: 2" in result.output
    assert "Items submitted to ingestion: 2" in result.output
    assert "Imported: 1" in result.output
    assert "Skipped duplicates: 1" in result.output
    assert "Companies persisted: 1" in result.output
    assert len(companies_for_project(project_id)) == 1


def test_dry_run_does_not_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, profile_id = create_project_and_profile()
    provider = FakeProvider(
        [provider_response(("Dry Run Company", "https://dry-run.example.com/"))]
    )
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id, persist=False)

    assert result.exit_code == 0, result.output
    assert "Dry run: True" in result.output
    assert "Companies persisted: 0" in result.output
    assert companies_for_project(project_id) == []
    assert_safe_output(result.output)


def test_rollback_does_not_leave_partial_companies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, profile_id = create_project_and_profile()
    provider = FakeProvider(
        [
            provider_response(
                ("Before Failure", "https://before-failure.example.com/"),
                ("After Failure", "https://after-failure.example.com/"),
            )
        ]
    )
    install_provider(monkeypatch, provider)
    original_flush = Session.flush
    company_flush_count = 0
    first_flush_reached_database = False

    def fail_company_flush(session: Session, objects: Any = None) -> None:
        nonlocal company_flush_count, first_flush_reached_database

        if any(isinstance(item, Company) for item in session.new):
            company_flush_count += 1

            if company_flush_count == 2:
                raise SQLAlchemyError("synthetic second company flush failure")

        original_flush(session, objects)

        if company_flush_count == 1:
            flushed_company_count = session.connection().execute(
                select(func.count()).select_from(Company)
            )
            first_flush_reached_database = flushed_company_count.scalar_one() == 1

    monkeypatch.setattr(Session, "flush", fail_company_flush)

    result = invoke_profile(profile_id)

    assert result.exit_code == 1
    assert company_flush_count == 2
    assert first_flush_reached_database is True
    assert "Rolled back: True" in result.output
    assert "Companies persisted: 0" in result.output
    assert companies_for_project(project_id) == []
    assert_safe_output(result.output)


def test_terminal_provider_stop_persists_partial_items_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, profile_id = create_project_and_profile(query_count=2)
    provider = FakeProvider(
        [
            provider_response(("Partial Company", "https://partial.example.com/")),
            DiscoveryProviderRateLimitError("unsafe provider detail"),
        ]
    )
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id, max_queries=2)

    assert result.exit_code == 1
    assert "Imported: 1" in result.output
    assert "Stopped Early: True" in result.output
    assert "Stop Reason: rate_limit_error" in result.output
    assert "Provider Error: Discovery provider rate limit exceeded." in result.output
    assert "unsafe provider detail" not in result.output
    assert len(companies_for_project(project_id)) == 1
    assert_safe_output(result.output)


def test_nonterminal_provider_error_with_valid_items_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, profile_id = create_project_and_profile(query_count=2)
    provider = FakeProvider(
        [
            DiscoveryProviderRequestError("unsafe request detail"),
            provider_response(("Recovered Company", "https://recovered.example.com/")),
        ]
    )
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id, max_queries=2)

    assert result.exit_code == 0, result.output
    assert "Imported: 1" in result.output
    assert "Stopped Early: False" in result.output
    assert "Provider Error Code: request_error" in result.output
    assert "Provider Error: Discovery provider request failed." in result.output
    assert "unsafe request detail" not in result.output
    assert len(companies_for_project(project_id)) == 1
    assert_safe_output(result.output)


def test_zero_adapted_items_is_successful_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, profile_id = create_project_and_profile()
    provider = FakeProvider([provider_response()])
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id)

    assert result.exit_code == 0, result.output
    assert "Ingestion attempted: False" in result.output
    assert "Items submitted to ingestion: 0" in result.output
    assert "Imported: 0" in result.output
    assert "Companies persisted: 0" in result.output
    assert companies_for_project(project_id) == []


def test_duplicate_detection_is_project_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    project_a_id, profile_id = create_project_and_profile()
    with SessionLocal() as session:
        project_b = Project(name="Other Project")
        session.add(project_b)
        session.flush()
        session.add(
            Company(
                project_id=project_b.id,
                name="Existing Elsewhere",
                website="https://shared.example.com/existing",
            )
        )
        session.commit()
        project_b_id = project_b.id
    provider = FakeProvider(
        [provider_response(("Project A Company", "https://shared.example.com/new"))]
    )
    install_provider(monkeypatch, provider)

    result = invoke_profile(profile_id)

    assert result.exit_code == 0, result.output
    assert "Imported: 1" in result.output
    assert "Skipped duplicates: 0" in result.output
    assert len(companies_for_project(project_a_id)) == 1
    assert len(companies_for_project(project_b_id)) == 1
    assert companies_for_project(project_a_id)[0].project_id == project_a_id


def test_cli_keeps_transaction_and_ingestion_boundaries() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")

    assert "CompanyIngestionService" not in source
    assert "session.add(" not in source
    assert "session.flush(" not in source
    assert "session.commit(" not in source
    assert "session.rollback(" not in source
    assert "Company(" not in source
    assert "deduplic" not in source.casefold()
    assert "normalize_website" not in source
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 0

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

import app.cli.company_enrichment as cli
from app.cli.main import app as main_app
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)
from app.modules.project.models import Project

runner = CliRunner()


class TrackingFakeProvider:
    provider_name = "fake"
    targets: list[CompanyEnrichmentTarget] = []

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        assert isinstance(target, CompanyEnrichmentTarget)
        assert not isinstance(target, Company)
        self.targets.append(target)
        return CompanyEnrichmentProviderResult(
            provider=self.provider_name,
            website=target.website,
            source_url=target.website,
            notes="Fake enrichment result.",
        )


class TrackingWebsiteProvider:
    provider_name = "website"
    targets: list[CompanyEnrichmentTarget] = []

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        self.targets.append(target)
        return CompanyEnrichmentProviderResult(
            provider=self.provider_name,
            source_url=target.website,
            email="website@example.com",
            notes="Static website enrichment parsed.",
        )


def create_project(*, company_websites: list[str | None]) -> tuple[int, list[int]]:
    with SessionLocal() as session:
        project = Project(name="CLI Project")
        session.add(project)
        session.flush()
        companies = [
            Company(
                project_id=project.id,
                name=f"Company {index}",
                website=website,
            )
            for index, website in enumerate(company_websites, start=1)
        ]
        session.add_all(companies)
        session.commit()
        return project.id, [company.id for company in companies]


def invoke(
    project_id: int,
    *,
    mode: str | None,
    provider: str = "fake",
    limit: int = 20,
) -> Any:
    arguments = [
        "run",
        "--project-id",
        str(project_id),
        "--provider",
        provider,
        "--limit",
        str(limit),
    ]
    if mode is not None:
        arguments.append(mode)
    return runner.invoke(cli.app, arguments)


def enrichment_count() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(CompanyEnrichment)) or 0


@pytest.mark.parametrize("mode", ["--dry-run", "--persist"])
def test_single_mode_works(mode: str) -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(project_id, mode=mode)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("modes", [[], ["--dry-run", "--persist"]])
def test_exactly_one_mode_is_required_and_invalid_mode_does_not_write(
    modes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = create_project(company_websites=[None])

    def forbidden_provider() -> None:
        raise AssertionError("Provider must not be opened for an invalid mode.")

    monkeypatch.setattr(cli, "FakeEnrichmentProvider", forbidden_provider)
    arguments = [
        "run",
        "--project-id",
        str(project_id),
        "--provider",
        "fake",
        *modes,
    ]
    result = runner.invoke(cli.app, arguments)
    assert result.exit_code == 1
    assert "Choose exactly one mode: --dry-run or --persist." in result.output
    assert enrichment_count() == 0


def test_unsupported_provider_exits_safely_without_writes() -> None:
    project_id, _ = create_project(company_websites=[None])
    result = invoke(project_id, mode="--persist", provider="unknown")
    assert result.exit_code == 1
    assert "Choose one of: fake, website" in result.output
    assert enrichment_count() == 0


def test_help_reflects_supported_website_provider() -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(project_id, mode="--dry-run", provider="unknown")
    assert result.exit_code == 1
    assert "fake, website" in result.output


@pytest.mark.parametrize(
    ("project_id", "limit", "message"),
    [
        (0, 20, "Project ID must be greater than zero."),
        (1, 0, "Limit must be between 1 and 100."),
        (1, 101, "Limit must be between 1 and 100."),
    ],
)
def test_numeric_validation(project_id: int, limit: int, message: str) -> None:
    result = invoke(project_id, mode="--persist", limit=limit)
    assert result.exit_code == 1
    assert message in result.output
    assert enrichment_count() == 0


def test_dry_run_writes_nothing_and_reports_safe_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, company_ids = create_project(company_websites=["https://one.example"])
    TrackingFakeProvider.targets = []
    monkeypatch.setattr(cli, "FakeEnrichmentProvider", TrackingFakeProvider)
    result = invoke(project_id, mode="--dry-run")
    assert result.exit_code == 0, result.output
    assert enrichment_count() == 0
    for expected in [
        "Dry run: True",
        "Persistence requested: False",
        "Provider: fake",
        f"Project ID: {project_id}",
        "Limit: 20",
        "Selected: 1",
        "Attempted: 1",
        "Created: 1",
        "Updated: 0",
        "Unchanged: 0",
        "Succeeded: 1",
        "Partial: 0",
        "Not found: 0",
        "Failed: 0",
        f"Company ID: {company_ids[0]}",
        "Company Name: Company 1",
        "Status: SUCCEEDED",
        "Changed fields: source_url",
    ]:
        assert expected in result.output
    assert [target.company_id for target in TrackingFakeProvider.targets] == company_ids


def test_website_dry_run_selects_provider_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, company_ids = create_project(company_websites=["https://one.example"])
    TrackingWebsiteProvider.targets = []
    monkeypatch.setattr(cli, "WebsiteEnrichmentProvider", TrackingWebsiteProvider)

    result = invoke(project_id, mode="--dry-run", provider="website")

    assert result.exit_code == 0, result.output
    assert enrichment_count() == 0
    assert "Provider: website" in result.output
    assert "Dry run: True" in result.output
    assert "Persistence requested: False" in result.output
    assert [target.company_id for target in TrackingWebsiteProvider.targets] == company_ids


def test_persist_creates_rows_through_real_service() -> None:
    project_id, company_ids = create_project(company_websites=["https://one.example", None])
    result = invoke(project_id, mode="--persist")
    assert result.exit_code == 0, result.output
    assert "Dry run: False" in result.output
    assert "Persistence requested: True" in result.output
    assert "Selected: 2" in result.output
    assert "Succeeded: 1" in result.output
    assert "Not found: 1" in result.output
    with SessionLocal() as session:
        rows = list(
            session.scalars(select(CompanyEnrichment).order_by(CompanyEnrichment.company_id))
        )
        assert [row.company_id for row in rows] == company_ids


def test_website_persist_selects_provider_and_writes_through_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, company_ids = create_project(company_websites=["https://one.example"])
    TrackingWebsiteProvider.targets = []
    monkeypatch.setattr(cli, "WebsiteEnrichmentProvider", TrackingWebsiteProvider)

    result = invoke(project_id, mode="--persist", provider="website")

    assert result.exit_code == 0, result.output
    assert "Provider: website" in result.output
    assert "Dry run: False" in result.output
    assert "Persistence requested: True" in result.output
    assert [target.company_id for target in TrackingWebsiteProvider.targets] == company_ids
    with SessionLocal() as session:
        rows = list(session.scalars(select(CompanyEnrichment)))
        assert [row.company_id for row in rows] == company_ids
        assert rows[0].email == "website@example.com"


def test_project_selection_and_limit_are_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, company_ids = create_project(company_websites=[None, None, None])
    other_project_id, other_company_ids = create_project(company_websites=[None])
    TrackingFakeProvider.targets = []
    monkeypatch.setattr(cli, "FakeEnrichmentProvider", TrackingFakeProvider)
    result = invoke(project_id, mode="--dry-run", limit=2)
    assert result.exit_code == 0, result.output
    assert "Selected: 2" in result.output
    assert [target.company_id for target in TrackingFakeProvider.targets] == company_ids[:2]
    assert other_company_ids[0] not in [
        target.company_id for target in TrackingFakeProvider.targets
    ]
    assert other_project_id != project_id


def test_zero_selected_companies_is_success() -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(project_id, mode="--dry-run")
    assert result.exit_code == 0, result.output
    assert "Selected: 0" in result.output
    assert "Attempted: 0" in result.output


def test_cli_persist_preserves_existing_manual_data() -> None:
    project_id, company_ids = create_project(company_websites=[None])
    with SessionLocal() as session:
        session.add(
            CompanyEnrichment(
                company_id=company_ids[0],
                email="manual@example.com",
                notes="Manual notes",
            )
        )
        session.commit()
    result = invoke(project_id, mode="--persist")
    assert result.exit_code == 0, result.output
    with SessionLocal() as session:
        enrichment = session.scalar(select(CompanyEnrichment))
        assert enrichment is not None
        assert enrichment.email == "manual@example.com"
        assert enrichment.notes == "Manual notes"


def test_cli_has_no_overwrite_option() -> None:
    result = runner.invoke(cli.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--overwrite" not in result.output


def test_company_enrichment_is_registered_in_root_cli() -> None:
    project_id, _ = create_project(company_websites=[])
    result = runner.invoke(
        main_app,
        [
            "company-enrichment",
            "run",
            "--project-id",
            str(project_id),
            "--provider",
            "fake",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Selected: 0" in result.output


def test_unexpected_provider_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, _ = create_project(company_websites=[None])

    class FailingProvider:
        provider_name = "fake"

        def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
            raise RuntimeError("API_KEY=secret <html> raw payload")

    monkeypatch.setattr(cli, "FakeEnrichmentProvider", FailingProvider)
    result = invoke(project_id, mode="--persist")
    assert result.exit_code == 1
    assert "Company enrichment failed safely." in result.output
    for unsafe in ["Traceback", "API_KEY", "secret", "<html>", "sqlite:///", "Settings("]:
        assert unsafe not in result.output


def test_unexpected_website_provider_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = create_project(company_websites=["https://one.example"])

    class FailingWebsiteProvider:
        provider_name = "website"

        def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
            raise RuntimeError("API_KEY=secret <html> raw payload traceback")

    monkeypatch.setattr(cli, "WebsiteEnrichmentProvider", FailingWebsiteProvider)
    result = invoke(project_id, mode="--dry-run", provider="website")
    assert result.exit_code == 1
    assert "Company enrichment failed safely." in result.output
    for unsafe in ["Traceback", "API_KEY", "secret", "<html>", "raw payload"]:
        assert unsafe not in result.output


def test_website_provider_result_errors_are_safely_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = create_project(company_websites=["https://one.example"])

    class SafeErrorWebsiteProvider:
        provider_name = "website"

        def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
            return CompanyEnrichmentProviderResult(
                provider=self.provider_name,
                source_url=target.website,
                errors=["Website request failed."],
            )

    monkeypatch.setattr(cli, "WebsiteEnrichmentProvider", SafeErrorWebsiteProvider)
    result = invoke(project_id, mode="--dry-run", provider="website")
    assert result.exit_code == 0, result.output
    assert "Error: Provider reported an enrichment error." in result.output
    assert "Website request failed." not in result.output


def test_cli_keeps_service_and_persistence_boundaries() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    for forbidden in [
        "session.add(",
        "session.flush(",
        "session.commit(",
        "session.rollback(",
        "CompanyEnrichment(",
        "normalize_public_url",
        "SerpApi",
        "serpapi",
        "httpx",
        "requests",
        "selenium",
        "playwright",
        "instagram",
        "linkedin",
        "socket",
        "getaddrinfo",
    ]:
        assert forbidden not in source
    assert "CompanyEnrichmentService" in source
    assert "CompanyEnrichmentRepository" in source


def test_website_provider_is_constructed_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed = False

    class TrackingConstructor:
        provider_name = "website"

        def __init__(self) -> None:
            nonlocal constructed
            constructed = True

        def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
            return CompanyEnrichmentProviderResult(provider=self.provider_name)

    monkeypatch.setattr(cli, "WebsiteEnrichmentProvider", TrackingConstructor)
    assert not constructed
    provider_instance = cli._get_enrichment_provider("website")
    assert constructed
    assert provider_instance.provider_name == "website"

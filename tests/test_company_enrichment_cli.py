from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

import app.cli.company_enrichment as cli
from app.cli.main import app as main_app
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment, EnrichmentStatus
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentSelectionOptions,
    CompanyEnrichmentTarget,
)
from app.modules.company_enrichment.service import CompanyEnrichmentService
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
    limit: int | None = 20,
    extra_args: tuple[str, ...] = (),
) -> Any:
    arguments = [
        "run",
        "--project-id",
        str(project_id),
        "--provider",
        provider,
    ]
    if limit is not None:
        arguments.extend(["--limit", str(limit)])
    arguments.extend(extra_args)
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

    result = invoke(project_id, mode="--dry-run", provider="website", limit=1)

    assert result.exit_code == 0, result.output
    assert enrichment_count() == 0
    assert "Provider: website" in result.output
    assert "Dry run: True" in result.output
    assert "Persistence requested: False" in result.output
    assert "Limit: 1" in result.output
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


def test_fake_without_limit_defaults_to_twenty() -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(project_id, mode="--dry-run", limit=None)
    assert result.exit_code == 0, result.output
    assert "Limit: 20" in result.output


def test_website_without_limit_exits_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = create_project(company_websites=["https://one.example"])

    def forbidden_provider() -> None:
        raise AssertionError("Website provider must not be constructed without --limit.")

    monkeypatch.setattr(cli, "WebsiteEnrichmentProvider", forbidden_provider)
    result = invoke(project_id, mode="--dry-run", provider="website", limit=None)
    assert result.exit_code == 1
    assert "--limit is required when using --provider website." in result.output
    assert enrichment_count() == 0


@pytest.mark.parametrize("days", [0, 3651])
def test_invalid_skip_recent_days_exits_safely(days: int) -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=("--skip-recent-days", str(days)),
    )
    assert result.exit_code == 1
    assert "Invalid company enrichment selection options." in result.output


def test_invalid_status_exits_safely() -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=("--status", "UNKNOWN"),
    )
    assert result.exit_code == 1
    assert "Invalid company enrichment selection options." in result.output


def test_invalid_company_id_exits_safely() -> None:
    project_id, _ = create_project(company_websites=[])
    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=("--company-id", "0"),
    )
    assert result.exit_code == 1
    assert "Invalid company enrichment selection options." in result.output


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("--only-missing",), CompanyEnrichmentSelectionOptions(only_missing=True)),
        (
            ("--skip-recent-days", "30"),
            CompanyEnrichmentSelectionOptions(skip_recent_days=30),
        ),
        (
            ("--status", "FAILED"),
            CompanyEnrichmentSelectionOptions(status=EnrichmentStatus.FAILED),
        ),
        (("--company-id", "123"), CompanyEnrichmentSelectionOptions(company_id=123)),
    ],
)
def test_each_selection_flag_is_passed_to_service(
    arguments: tuple[str, ...],
    expected: CompanyEnrichmentSelectionOptions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = create_project(company_websites=[])
    captured: list[CompanyEnrichmentSelectionOptions] = []
    original = CompanyEnrichmentService.enrich_project_companies

    def tracking_enrich(self: CompanyEnrichmentService, *args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs["selection_options"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CompanyEnrichmentService, "enrich_project_companies", tracking_enrich)
    result = invoke(project_id, mode="--dry-run", extra_args=arguments)

    assert result.exit_code == 0, result.output
    assert captured == [expected]


def test_combined_selection_flags_are_passed_to_service_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, company_ids = create_project(company_websites=["https://one.example"])
    captured: list[CompanyEnrichmentSelectionOptions] = []
    original = CompanyEnrichmentService.enrich_project_companies

    def tracking_enrich(self: CompanyEnrichmentService, *args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs["selection_options"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CompanyEnrichmentService, "enrich_project_companies", tracking_enrich)
    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=(
            "--only-missing",
            "--skip-recent-days",
            "30",
            "--status",
            "pending",
            "--company-id",
            str(company_ids[0]),
        ),
    )

    assert result.exit_code == 0, result.output
    assert captured == [
        CompanyEnrichmentSelectionOptions(
            only_missing=True,
            skip_recent_days=30,
            status=EnrichmentStatus.PENDING,
            company_id=company_ids[0],
        )
    ]
    for expected in [
        "Only missing: True",
        "Skip recent days: 30",
        "Status filter: PENDING",
        f"Company ID filter: {company_ids[0]}",
        "Matched: 0",
        "Skipped by filters: 1",
    ]:
        assert expected in result.output


def test_only_missing_dry_run_calls_provider_only_for_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, company_ids = create_project(
        company_websites=["https://missing.example", "https://complete.example"]
    )
    with SessionLocal() as session:
        session.add(
            CompanyEnrichment(
                company_id=company_ids[1],
                email="complete@example.com",
                phone="+1 212 555 0199",
                instagram_url="https://instagram.com/complete",
                linkedin_url="https://linkedin.com/company/complete",
                contact_page_url="https://complete.example/contact",
                about_page_url="https://complete.example/about",
                source_url="https://complete.example",
            )
        )
        session.commit()
    TrackingFakeProvider.targets = []
    monkeypatch.setattr(cli, "FakeEnrichmentProvider", TrackingFakeProvider)

    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=("--only-missing",),
    )

    assert result.exit_code == 0, result.output
    assert [target.company_id for target in TrackingFakeProvider.targets] == [company_ids[0]]
    assert enrichment_count() == 1


def test_skip_recent_days_can_select_zero_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, company_ids = create_project(company_websites=["https://recent.example"])
    with SessionLocal() as session:
        session.add(
            CompanyEnrichment(
                company_id=company_ids[0],
                website_checked_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        session.commit()
    TrackingFakeProvider.targets = []
    monkeypatch.setattr(cli, "FakeEnrichmentProvider", TrackingFakeProvider)

    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=("--skip-recent-days", "30"),
    )

    assert result.exit_code == 0, result.output
    assert "Selected: 0" in result.output
    assert "Attempted: 0" in result.output
    assert TrackingFakeProvider.targets == []


def test_wrong_project_company_id_selects_zero() -> None:
    project_id, _ = create_project(company_websites=[])
    _, other_company_ids = create_project(company_websites=["https://other.example"])
    result = invoke(
        project_id,
        mode="--dry-run",
        extra_args=("--company-id", str(other_company_ids[0])),
    )
    assert result.exit_code == 0, result.output
    assert "Selected: 0" in result.output
    assert "Attempted: 0" in result.output


def test_selection_flags_preserve_dry_run_and_persist_boundaries() -> None:
    dry_project_id, _ = create_project(company_websites=["https://dry.example"])
    dry_result = invoke(
        dry_project_id,
        mode="--dry-run",
        extra_args=("--only-missing",),
    )
    assert dry_result.exit_code == 0, dry_result.output
    assert enrichment_count() == 0

    persist_project_id, company_ids = create_project(company_websites=["https://persist.example"])
    persist_result = invoke(
        persist_project_id,
        mode="--persist",
        extra_args=("--only-missing", "--company-id", str(company_ids[0])),
    )
    assert persist_result.exit_code == 0, persist_result.output
    with SessionLocal() as session:
        row = session.scalar(
            select(CompanyEnrichment).where(CompanyEnrichment.company_id == company_ids[0])
        )
        assert row is not None

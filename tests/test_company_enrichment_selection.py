import ast
from collections import deque
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment, EnrichmentStatus
from app.modules.company_enrichment.repository import CompanyEnrichmentRepository
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentSelectionOptions,
    CompanyEnrichmentTarget,
)
from app.modules.company_enrichment.service import CompanyEnrichmentService
from app.modules.project.models import Project

NOW = datetime(2026, 7, 15, tzinfo=UTC)
USEFUL_VALUES = {
    "email": "manual@example.com",
    "phone": "+1 212 555 0199",
    "instagram_url": "https://instagram.com/manual",
    "linkedin_url": "https://linkedin.com/company/manual",
    "contact_page_url": "https://manual.example/contact",
    "about_page_url": "https://manual.example/about",
    "source_url": "https://manual.example",
}


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as database_session:
        yield database_session


class FakeProvider:
    provider_name = "fake"

    def __init__(self, outcomes: list[CompanyEnrichmentProviderResult]) -> None:
        self.outcomes = deque(outcomes)
        self.targets: list[CompanyEnrichmentTarget] = []

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        self.targets.append(target)
        return self.outcomes.popleft()


def provider_result(**values: object) -> CompanyEnrichmentProviderResult:
    return CompanyEnrichmentProviderResult(provider="fake", **values)


def create_project(session: Session, name: str = "Project") -> Project:
    project = Project(name=name)
    session.add(project)
    session.flush()
    return project


def create_company(session: Session, project: Project, name: str) -> Company:
    item = Company(project_id=project.id, name=name, website=f"https://{name}.example")
    session.add(item)
    session.flush()
    return item


def add_enrichment(session: Session, item: Company, **values: object) -> CompanyEnrichment:
    enrichment = CompanyEnrichment(company_id=item.id, **values)
    session.add(enrichment)
    session.flush()
    return enrichment


def repository(session: Session) -> CompanyEnrichmentRepository:
    return CompanyEnrichmentRepository(session)


def service(session: Session) -> CompanyEnrichmentService:
    return CompanyEnrichmentService(repository(session))


@pytest.mark.parametrize(
    "values",
    [
        {"skip_recent_days": 0},
        {"skip_recent_days": 3651},
        {"company_id": 0},
        {"company_id": -1},
        {"status": "UNKNOWN"},
    ],
)
def test_selection_options_validate_bounds(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CompanyEnrichmentSelectionOptions.model_validate(values)


@pytest.mark.parametrize("limit", [0, 101])
def test_service_validates_selection_limit(session: Session, limit: int) -> None:
    project = create_project(session)
    with pytest.raises(ValueError, match="between 1 and 100"):
        service(session).enrich_project_companies(
            project.id,
            FakeProvider([]),
            limit=limit,
            dry_run=True,
        )


def test_only_missing_semantics_and_counts(session: Session) -> None:
    project = create_project(session)
    without_row = create_company(session, project, "without-row")
    missing_useful = create_company(session, project, "missing-useful")
    fully_filled = create_company(session, project, "fully-filled")
    add_enrichment(session, missing_useful, notes="Notes", last_error="Safe error")
    add_enrichment(session, fully_filled, **USEFUL_VALUES)
    session.commit()

    selection = repository(session).select_companies_for_enrichment(
        project.id,
        10,
        options=CompanyEnrichmentSelectionOptions(only_missing=True),
    )

    assert [item.id for item in selection.companies] == [without_row.id, missing_useful.id]
    assert selection.matched_count == 2
    assert selection.selected_count == 2
    assert selection.skipped_by_filters_count == 1


def test_only_missing_checks_every_useful_field(session: Session) -> None:
    project = create_project(session)
    fields = tuple(USEFUL_VALUES)
    companies = [
        create_company(session, project, f"missing-{index}") for index in range(len(fields))
    ]
    for item, missing_field in zip(companies, fields, strict=True):
        values: dict[str, object] = dict(USEFUL_VALUES)
        values[missing_field] = None
        add_enrichment(session, item, **values)
    session.commit()

    selection = repository(session).select_companies_for_enrichment(
        project.id,
        100,
        options=CompanyEnrichmentSelectionOptions(only_missing=True),
    )
    assert [item.id for item in selection.companies] == [item.id for item in companies]


def test_skip_recent_days_semantics_and_deterministic_cutoff(session: Session) -> None:
    project = create_project(session)
    without_row = create_company(session, project, "without-row")
    unchecked = create_company(session, project, "unchecked")
    old = create_company(session, project, "old")
    fresh = create_company(session, project, "fresh")
    add_enrichment(session, unchecked)
    add_enrichment(session, old, website_checked_at=NOW - timedelta(days=31))
    add_enrichment(session, fresh, website_checked_at=NOW - timedelta(days=1))
    session.commit()
    provider = FakeProvider([provider_result(), provider_result(), provider_result()])

    run = service(session).enrich_project_companies(
        project.id,
        provider,
        limit=10,
        dry_run=True,
        selection_options=CompanyEnrichmentSelectionOptions(skip_recent_days=30),
        now=NOW,
    )

    assert [target.company_id for target in provider.targets] == [
        without_row.id,
        unchecked.id,
        old.id,
    ]
    assert run.matched == 3
    assert run.selected == 3
    assert run.skipped_by_filters == 1


@pytest.mark.parametrize("status", list(EnrichmentStatus))
def test_status_selects_exact_existing_status(session: Session, status: EnrichmentStatus) -> None:
    project = create_project(session)
    without_row = create_company(session, project, "without-row")
    matches = create_company(session, project, "matches")
    other = create_company(session, project, "other")
    add_enrichment(session, matches, enrichment_status=status)
    different = (
        EnrichmentStatus.FAILED if status != EnrichmentStatus.FAILED else EnrichmentStatus.SUCCEEDED
    )
    add_enrichment(session, other, enrichment_status=different)
    session.commit()

    selection = repository(session).select_companies_for_enrichment(
        project.id,
        10,
        options=CompanyEnrichmentSelectionOptions(status=status),
    )

    assert [item.id for item in selection.companies] == [matches.id]
    assert without_row.id not in [item.id for item in selection.companies]
    assert selection.skipped_by_filters_count == 2


def test_company_id_is_project_scoped_and_combines_with_filters(session: Session) -> None:
    project = create_project(session, "First")
    other_project = create_project(session, "Second")
    selected = create_company(session, project, "selected")
    other = create_company(session, other_project, "other")
    add_enrichment(session, selected, **USEFUL_VALUES)
    session.commit()

    excluded_by_filter = repository(session).select_companies_for_enrichment(
        project.id,
        10,
        options=CompanyEnrichmentSelectionOptions(company_id=selected.id, only_missing=True),
    )
    wrong_project = repository(session).select_companies_for_enrichment(
        project.id,
        10,
        options=CompanyEnrichmentSelectionOptions(company_id=other.id),
    )
    exact = repository(session).select_companies_for_enrichment(
        project.id,
        10,
        options=CompanyEnrichmentSelectionOptions(company_id=selected.id),
    )

    assert excluded_by_filter.selected_count == 0
    assert wrong_project.selected_count == 0
    assert [item.id for item in exact.companies] == [selected.id]


def test_combined_filters_use_and_semantics(session: Session) -> None:
    project = create_project(session)
    old_missing = create_company(session, project, "old-missing")
    fresh_missing = create_company(session, project, "fresh-missing")
    old_full = create_company(session, project, "old-full")
    add_enrichment(
        session,
        old_missing,
        enrichment_status=EnrichmentStatus.PARTIAL,
        website_checked_at=NOW - timedelta(days=40),
    )
    add_enrichment(
        session,
        fresh_missing,
        enrichment_status=EnrichmentStatus.PARTIAL,
        website_checked_at=NOW - timedelta(days=1),
    )
    add_enrichment(
        session,
        old_full,
        enrichment_status=EnrichmentStatus.SUCCEEDED,
        website_checked_at=NOW - timedelta(days=40),
        **USEFUL_VALUES,
    )
    session.commit()

    selection = repository(session).select_companies_for_enrichment(
        project.id,
        10,
        options=CompanyEnrichmentSelectionOptions(
            only_missing=True,
            status=EnrichmentStatus.PARTIAL,
            skip_recent_days=30,
        ),
        checked_before=NOW - timedelta(days=30),
    )
    assert [item.id for item in selection.companies] == [old_missing.id]


def test_filters_apply_before_limit_and_limit_does_not_increase_skipped_count(
    session: Session,
) -> None:
    project = create_project(session)
    excluded = create_company(session, project, "excluded")
    first_match = create_company(session, project, "first-match")
    second_match = create_company(session, project, "second-match")
    add_enrichment(session, excluded, **USEFUL_VALUES)
    session.commit()

    selection = repository(session).select_companies_for_enrichment(
        project.id,
        1,
        options=CompanyEnrichmentSelectionOptions(only_missing=True),
    )

    assert [item.id for item in selection.companies] == [first_match.id]
    assert second_match.id > first_match.id
    assert selection.matched_count == 2
    assert selection.selected_count == 1
    assert selection.skipped_by_filters_count == 1


def test_service_calls_provider_only_for_selected_and_handles_zero_selection(
    session: Session,
) -> None:
    project = create_project(session)
    selected = create_company(session, project, "selected")
    excluded = create_company(session, project, "excluded")
    add_enrichment(session, excluded, **USEFUL_VALUES)
    session.commit()
    provider = FakeProvider([provider_result(email="selected@example.com")])

    run = service(session).enrich_project_companies(
        project.id,
        provider,
        limit=10,
        dry_run=True,
        selection_options=CompanyEnrichmentSelectionOptions(only_missing=True),
    )
    assert [target.company_id for target in provider.targets] == [selected.id]
    assert run.attempted == 1

    zero_provider = FakeProvider([])
    zero = service(session).enrich_project_companies(
        project.id,
        zero_provider,
        limit=10,
        dry_run=True,
        selection_options=CompanyEnrichmentSelectionOptions(company_id=999_999),
    )
    assert zero.selected == zero.attempted == 0
    assert zero_provider.targets == []


def test_selection_dry_run_changes_no_existing_metadata(session: Session) -> None:
    project = create_project(session)
    item = create_company(session, project, "selected")
    checked_at = NOW - timedelta(days=100)
    enrichment = add_enrichment(
        session,
        item,
        enrichment_status=EnrichmentStatus.FAILED,
        website_checked_at=checked_at,
        last_error="Existing safe error.",
    )
    session.commit()
    before_count = session.scalar(select(func.count()).select_from(CompanyEnrichment))

    service(session).enrich_project_companies(
        project.id,
        FakeProvider([provider_result(email="new@example.com")]),
        limit=1,
        dry_run=True,
        selection_options=CompanyEnrichmentSelectionOptions(company_id=item.id),
    )
    session.refresh(enrichment)
    assert session.scalar(select(func.count()).select_from(CompanyEnrichment)) == before_count
    assert enrichment.email is None
    assert enrichment.website_checked_at is not None
    assert enrichment.website_checked_at.replace(tzinfo=UTC) == checked_at
    assert enrichment.enrichment_status == EnrichmentStatus.FAILED
    assert enrichment.last_error == "Existing safe error."


def test_selection_persist_protects_all_manual_non_null_fields(session: Session) -> None:
    project = create_project(session)
    item = create_company(session, project, "selected")
    manual = {**USEFUL_VALUES, "notes": "Manual notes"}
    enrichment = add_enrichment(session, item, **manual)
    session.commit()
    provider = FakeProvider(
        [
            provider_result(
                email="new@example.com",
                phone="+1 646 555 0100",
                instagram_url="https://instagram.com/new",
                linkedin_url="https://linkedin.com/company/new",
                contact_page_url="https://new.example/contact",
                about_page_url="https://new.example/about",
                source_url="https://new.example",
                notes="Provider notes",
            )
        ]
    )

    service(session).enrich_project_companies(
        project.id,
        provider,
        limit=1,
        dry_run=False,
        selection_options=CompanyEnrichmentSelectionOptions(company_id=item.id),
    )
    session.refresh(enrichment)

    for field, value in USEFUL_VALUES.items():
        assert getattr(enrichment, field) == value
    assert enrichment.notes == "Manual notes"


def test_selection_persist_fills_empty_fields_and_sanitizes_errors(session: Session) -> None:
    project = create_project(session)
    item = create_company(session, project, "selected")
    enrichment = add_enrichment(
        session,
        item,
        about_page_url=None,
        notes="Manual notes",
    )
    session.commit()
    provider = FakeProvider(
        [
            provider_result(
                about_page_url="https://new.example/about",
                notes="Provider notes",
                errors=["API_KEY=secret raw payload"],
            )
        ]
    )

    run = service(session).enrich_project_companies(
        project.id,
        provider,
        limit=1,
        dry_run=False,
        selection_options=CompanyEnrichmentSelectionOptions(company_id=item.id),
    )
    session.refresh(enrichment)

    assert enrichment.about_page_url == "https://new.example/about"
    assert enrichment.notes == "Manual notes"
    assert enrichment.website_checked_at is not None
    assert enrichment.last_error == "Provider reported an enrichment error."
    assert run.items[0].errors == ["Provider reported an enrichment error."]
    assert "secret" not in repr(run)


def test_selection_foundation_has_no_network_or_forbidden_automation_imports() -> None:
    production_sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "app/modules/company_enrichment/repository.py",
            "app/modules/company_enrichment/service.py",
        )
    ).casefold()
    for forbidden in (
        "serpapi",
        "httpx",
        "requests",
        "socket",
        "getaddrinfo",
        "selenium",
        "playwright",
        "instagram_api",
        "linkedin_api",
        "send_message",
        "openai",
    ):
        assert forbidden not in production_sources

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported_modules = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_modules.isdisjoint({"httpx", "requests", "socket", "selenium", "playwright"})

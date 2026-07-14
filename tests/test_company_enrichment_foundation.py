from collections import deque
from collections.abc import Generator

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.company_enrichment.normalization import (
    normalize_email,
    normalize_instagram_url,
    normalize_linkedin_company_url,
    normalize_phone,
    normalize_public_url,
)
from app.modules.company_enrichment.provider_interfaces import EnrichmentProviderError
from app.modules.company_enrichment.repository import CompanyEnrichmentRepository
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)
from app.modules.company_enrichment.service import CompanyEnrichmentService
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as database_session:
        yield database_session


class FakeProvider:
    provider_name = "fake"

    def __init__(self, outcomes: list[CompanyEnrichmentProviderResult | Exception]) -> None:
        self.outcomes = deque(outcomes)
        self.targets: list[CompanyEnrichmentTarget] = []

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        assert isinstance(target, CompanyEnrichmentTarget)
        assert not isinstance(target, Company)
        self.targets.append(target)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def result(**values: object) -> CompanyEnrichmentProviderResult:
    return CompanyEnrichmentProviderResult(provider="fake", **values)


def company(session: Session, *, project: Project | None = None, **values: object) -> Company:
    if project is None:
        project = Project(name="Project")
        session.add(project)
        session.flush()
    item = Company(project_id=project.id, name="Company", **values)
    session.add(item)
    session.commit()
    return item


def service(session: Session) -> CompanyEnrichmentService:
    return CompanyEnrichmentService(CompanyEnrichmentRepository(session))


def test_repository_create_update_get_or_create_and_timestamps(session: Session) -> None:
    item = company(session)
    repository = CompanyEnrichmentRepository(session)
    enrichment = repository.create(company_id=item.id, email="first@example.com")
    session.commit()

    assert enrichment.created_at is not None
    assert enrichment.updated_at is not None
    existing, created = repository.get_or_create_for_company(item.id)
    assert existing.id == enrichment.id
    assert created is False
    repository.update(existing, email="second@example.com")
    session.commit()
    assert repository.get_by_company_id(item.id).email == "second@example.com"  # type: ignore[union-attr]


def test_company_id_is_unique(session: Session) -> None:
    item = company(session)
    session.add_all([CompanyEnrichment(company_id=item.id), CompanyEnrichment(company_id=item.id)])
    with pytest.raises(IntegrityError):
        session.commit()


def test_company_delete_cascades_enrichment(session: Session) -> None:
    item = company(session)
    enrichment = CompanyEnrichment(company_id=item.id)
    session.add(enrichment)
    session.commit()
    enrichment_id = enrichment.id
    session.delete(item)
    session.commit()
    assert session.get(CompanyEnrichment, enrichment_id) is None


def test_list_for_project_is_scoped_and_ordered(session: Session) -> None:
    first_project = Project(name="First")
    second_project = Project(name="Second")
    session.add_all([first_project, second_project])
    session.flush()
    first = company(session, project=first_project)
    second = company(session, project=first_project)
    other = company(session, project=second_project)
    session.add_all([CompanyEnrichment(company_id=value.id) for value in (first, second, other)])
    session.commit()
    found = CompanyEnrichmentRepository(session).list_for_project(first_project.id, 10)
    assert [value.company_id for value in found] == [first.id, second.id]


def test_dry_run_has_zero_writes_and_uses_pure_target(session: Session) -> None:
    item = company(session)
    provider = FakeProvider([result(email="INFO@Example.COM")])
    run = service(session).enrich_company(item, provider, dry_run=True)
    assert run.status == "SUCCEEDED"
    assert run.created is True
    assert session.scalar(select(func.count()).select_from(CompanyEnrichment)) == 0
    assert provider.targets[0].model_dump() == {
        "company_id": item.id,
        "company_name": "Company",
        "website": None,
        "country": None,
        "city": None,
    }


def test_persist_creates_then_updates_without_duplicate(session: Session) -> None:
    item = company(session)
    provider = FakeProvider([result(email="info@example.com"), result(phone="123 456 7890")])
    first = service(session).enrich_company(item, provider, dry_run=False)
    second = service(session).enrich_company(item, provider, dry_run=False)
    enrichment = session.scalar(select(CompanyEnrichment))
    assert first.created is True
    assert second.updated is True
    assert enrichment is not None
    assert enrichment.email == "info@example.com"
    assert enrichment.phone == "123 456 7890"
    assert enrichment.website_checked_at is not None
    assert session.scalar(select(func.count()).select_from(CompanyEnrichment)) == 1


def test_manual_values_are_preserved_unless_overwrite(session: Session) -> None:
    item = company(session, website="https://manual.example")
    enrichment = CompanyEnrichment(company_id=item.id, email="manual@example.com")
    session.add(enrichment)
    session.commit()
    provider = FakeProvider(
        [
            result(website="https://new.example/", email="new@example.com"),
            result(website="https://new.example/", email="new@example.com"),
        ]
    )
    enrichment_service = service(session)
    enrichment_service.enrich_company(item, provider, dry_run=False)
    assert item.website == "https://manual.example"
    assert enrichment.email == "manual@example.com"
    enrichment_service.enrich_company(item, provider, dry_run=False, overwrite=True)
    assert item.website == "https://new.example"
    assert enrichment.email == "new@example.com"


def test_company_website_is_filled_only_when_null(session: Session) -> None:
    item = company(session)
    provider = FakeProvider([result(website="https://Example.COM/")])
    service(session).enrich_company(item, provider, dry_run=False)
    assert item.website == "https://example.com"


@pytest.mark.parametrize(
    ("provider_result", "expected"),
    [
        (result(), "NOT_FOUND"),
        (result(email="info@example.com", errors=["unsafe raw detail"]), "PARTIAL"),
    ],
)
def test_status_outcomes(
    session: Session, provider_result: CompanyEnrichmentProviderResult, expected: str
) -> None:
    item = company(session)
    run = service(session).enrich_company(item, FakeProvider([provider_result]), dry_run=False)
    assert run.status == expected
    assert "unsafe raw detail" not in repr(run)


def test_controlled_provider_error_is_saved_safely(session: Session) -> None:
    item = company(session)
    run = service(session).enrich_company(
        item,
        FakeProvider([EnrichmentProviderError("API_KEY=secret raw payload")]),
        dry_run=False,
    )
    enrichment = session.scalar(select(CompanyEnrichment))
    assert run.status == "FAILED"
    assert enrichment is not None
    assert enrichment.last_error == "Enrichment provider failed."
    assert "secret" not in repr(run)


def test_project_limit_and_company_order(session: Session) -> None:
    project = Project(name="Project")
    session.add(project)
    session.flush()
    companies = [company(session, project=project) for _ in range(3)]
    provider = FakeProvider([result(), result()])
    run = service(session).enrich_project_companies(project.id, provider, limit=2, dry_run=True)
    assert run.selected == 2
    assert [target.company_id for target in provider.targets] == [
        value.id for value in companies[:2]
    ]


def test_provider_result_is_pure_schema() -> None:
    provider_result = result(email="info@example.com")
    assert isinstance(provider_result, BaseModel)
    assert provider_result.model_dump()["email"] == "info@example.com"
    assert not hasattr(provider_result, "session")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" HTTPS://Exämple.COM/contact/#team ", "https://xn--exmple-cua.com/contact"),
        ("https://example.com/", "https://example.com"),
    ],
)
def test_normalize_public_url(raw: str, expected: str) -> None:
    assert normalize_public_url(raw) == expected


@pytest.mark.parametrize(
    "raw", ["javascript:alert(1)", "ftp://example.com", "https://u:p@example.com"]
)
def test_normalize_public_url_rejects_unsafe_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_public_url(raw)


def test_social_url_normalization() -> None:
    assert (
        normalize_instagram_url("https://www.instagram.com/Example/")
        == "https://instagram.com/Example"
    )
    assert normalize_linkedin_company_url("https://www.linkedin.com/company/Example/") == (
        "https://linkedin.com/company/Example"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "https://instagram.com/p/abc",
        "https://instagram.com/reel/abc",
        "https://instagram.com/stories/user",
        "https://instagram.com/login",
    ],
)
def test_instagram_rejects_non_profile_urls(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_instagram_url(raw)


def test_linkedin_rejects_personal_profile() -> None:
    with pytest.raises(ValueError):
        normalize_linkedin_company_url("https://linkedin.com/in/person")


def test_email_and_phone_normalization() -> None:
    assert normalize_email(" User@EXAMPLE.COM ") == "User@example.com"
    assert normalize_phone(" +1   (234) 567-8901 ") == "+1 (234) 567-8901"
    with pytest.raises(ValueError):
        normalize_email("not-an-email")
    with pytest.raises(ValueError):
        normalize_phone("123")

from collections.abc import Callable, Generator
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.service import (
    ContactDiscoveryRunResult,
    ContactDiscoveryService,
)
from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProviderResult
from app.modules.project.models import Project


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as database_session:
        yield database_session


class FakeProvider:
    provider_name = "fake-website"

    def __init__(
        self,
        result: WebsiteContactDiscoveryProviderResult | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or WebsiteContactDiscoveryProviderResult(
            attempted_pages=1, successful_pages=1
        )
        self.error = error
        self.calls: list[tuple[int, str]] = []

    def discover(
        self, *, company_id: int, website_url: str
    ) -> WebsiteContactDiscoveryProviderResult:
        self.calls.append((company_id, website_url))
        if self.error is not None:
            raise self.error
        return self.result


def create_company(session: Session, name: str = "Company") -> Company:
    project = Project(name=f"{name} Project")
    session.add(project)
    session.flush()
    company = Company(project_id=project.id, name=name)
    session.add(company)
    session.flush()
    return company


def candidate(company_id: int, **values: object) -> ContactDiscoveryCandidateCreate:
    data: dict[str, object] = {
        "company_id": company_id,
        "name": "Ada Lovelace",
        "title": "Director",
        "email": "ada@example.com",
        "phone": None,
        "source_url": "https://example.com/team?ref=nav#people",
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 60,
    }
    data.update(values)
    return ContactDiscoveryCandidateCreate(**data)


def provider_result(
    *candidates: ContactDiscoveryCandidateCreate,
    attempted_pages: int = 1,
    successful_pages: int = 1,
    errors: tuple[str, ...] = (),
    selected_urls: int = 0,
    limited_link_scan: bool = False,
) -> WebsiteContactDiscoveryProviderResult:
    return WebsiteContactDiscoveryProviderResult(
        candidates=candidates,
        attempted_pages=attempted_pages,
        successful_pages=successful_pages,
        errors=errors,
        selected_urls=selected_urls,
        limited_link_scan=limited_link_scan,
    )


def service(session: Session, provider: FakeProvider) -> ContactDiscoveryService:
    return ContactDiscoveryService(ContactDiscoveryRepository(session), provider)


def run(
    session: Session,
    company: Company,
    result: WebsiteContactDiscoveryProviderResult,
    *,
    dry_run: bool = True,
) -> ContactDiscoveryRunResult:
    return service(session, FakeProvider(result)).run(
        company_id=company.id,
        website_url="https://example.com",
        dry_run=dry_run,
    )


def test_run_result_is_typed_frozen_safe_and_preserves_provider_metadata(session: Session) -> None:
    company = create_company(session)
    result = run(
        session,
        company,
        provider_result(
            candidate(company.id),
            attempted_pages=3,
            successful_pages=2,
            selected_urls=2,
            limited_link_scan=True,
        ),
    )
    assert isinstance(result, ContactDiscoveryRunResult)
    assert result.company_id == company.id
    assert result.dry_run is True
    assert result.attempted_pages == 3
    assert result.successful_pages == 2
    assert result.selected_urls == 2
    assert result.limited_link_scan is True
    assert isinstance(result.candidates, tuple)
    assert isinstance(result.errors, tuple)
    assert not hasattr(result, "session")
    with pytest.raises(FrozenInstanceError):
        result.status = ContactDiscoveryStatus.FAILED  # type: ignore[misc]


@pytest.mark.parametrize(
    ("result_factory", "expected"),
    [
        (
            lambda company_id: provider_result(candidate(company_id)),
            ContactDiscoveryStatus.SUCCEEDED,
        ),
        (
            lambda company_id: provider_result(
                candidate(company_id), errors=("secondary_page_fetch_failed",)
            ),
            ContactDiscoveryStatus.PARTIAL,
        ),
        (lambda _company_id: provider_result(), ContactDiscoveryStatus.NOT_FOUND),
        (
            lambda _company_id: provider_result(errors=("page_parse_failed",)),
            ContactDiscoveryStatus.PARTIAL,
        ),
        (
            lambda _company_id: provider_result(
                attempted_pages=1,
                successful_pages=0,
                errors=("homepage_fetch_failed",),
            ),
            ContactDiscoveryStatus.FAILED,
        ),
    ],
)
def test_status_mapping(
    session: Session,
    result_factory: Callable[[int], WebsiteContactDiscoveryProviderResult],
    expected: ContactDiscoveryStatus,
) -> None:
    company = create_company(session)
    assert run(session, company, result_factory(company.id)).status == expected


def test_provider_exception_is_sanitized_and_does_not_catch_base_exception(
    session: Session,
) -> None:
    company = create_company(session)
    failing = FakeProvider(error=RuntimeError("secret URL body headers cookies traceback"))
    result = service(session, failing).run(
        company_id=company.id, website_url="https://secret.example", dry_run=True
    )
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_failed",)
    assert "secret" not in repr(result)

    interrupted = FakeProvider(error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(session, interrupted).run(
            company_id=company.id, website_url="https://example.com", dry_run=True
        )


@pytest.mark.parametrize(
    "invalid",
    [
        WebsiteContactDiscoveryProviderResult(attempted_pages=-1),
        WebsiteContactDiscoveryProviderResult(attempted_pages=1, successful_pages=-1),
        WebsiteContactDiscoveryProviderResult(attempted_pages=1, successful_pages=2),
        WebsiteContactDiscoveryProviderResult(attempted_pages=0, successful_pages=0),
    ],
)
def test_invalid_counters_fail_without_candidates(
    session: Session, invalid: WebsiteContactDiscoveryProviderResult
) -> None:
    company = create_company(session)
    result = run(session, company, invalid)
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_invalid_result",)
    assert result.candidates == ()


def test_mismatched_candidate_invalidates_entire_result(session: Session) -> None:
    company = create_company(session)
    other = create_company(session, "Other")
    result = run(
        session,
        company,
        provider_result(candidate(company.id), candidate(other.id)),
        dry_run=False,
    )
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.candidates == ()
    assert result.candidate_upserts == 0
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0


@pytest.mark.parametrize("invalid_first", [False, True])
def test_invalid_identity_invalidates_complete_result_before_any_persist_upsert(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    invalid_first: bool,
) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    existing = repository.upsert_candidate(
        company.id,
        candidate(
            company.id,
            name="Existing",
            email="existing@example.com",
            phone="123",
            confidence=75,
        ),
    )
    session.commit()
    existing_row = repository.get_candidate(existing.candidate.id)
    assert existing_row is not None
    existing_updated_at = existing_row.updated_at

    valid = candidate(company.id, email="valid@example.com")
    invalid = candidate(company.id, email=None, source_url=None)
    candidates = (invalid, valid) if invalid_first else (valid, invalid)
    upsert_calls = 0
    original_upsert = repository.upsert_candidate

    def record_upsert(company_id: int, value: ContactDiscoveryCandidateCreate) -> object:
        nonlocal upsert_calls
        upsert_calls += 1
        return original_upsert(company_id, value)

    monkeypatch.setattr(repository, "upsert_candidate", record_upsert)
    result = ContactDiscoveryService(repository, FakeProvider(provider_result(*candidates))).run(
        company_id=company.id,
        website_url="https://example.com",
        dry_run=False,
    )
    session.commit()

    state = repository.get_state_by_company_id(company.id)
    rows = repository.list_candidates_for_company(company.id)
    session.refresh(existing_row)
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_invalid_result",)
    assert result.candidates == ()
    assert result.candidate_upserts == 0
    assert result.state_persisted is True
    assert upsert_calls == 0
    assert state is not None
    assert state.discovery_status == ContactDiscoveryStatus.FAILED
    assert state.last_error == "provider_invalid_result"
    assert [row.email for row in rows] == ["existing@example.com"]
    assert existing_row.name == "Existing"
    assert existing_row.phone == "123"
    assert existing_row.confidence == 75
    assert existing_row.updated_at == existing_updated_at


def test_invalid_identity_dry_run_remains_mutation_free_after_outer_commit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    existing = repository.upsert_candidate(
        company.id, candidate(company.id, email="existing@example.com", confidence=70)
    )
    state = repository.update_state(
        company.id,
        provider="existing-provider",
        discovery_status=ContactDiscoveryStatus.SUCCEEDED,
        checked_at=datetime(2025, 2, 3, tzinfo=UTC),
        last_error=None,
    )
    session.commit()
    existing_row = repository.get_candidate(existing.candidate.id)
    assert existing_row is not None
    session.refresh(state)
    state_snapshot = (
        state.provider,
        state.discovery_status,
        state.checked_at,
        state.last_error,
        state.updated_at,
    )
    candidate_snapshot = (
        existing_row.name,
        existing_row.confidence,
        existing_row.updated_at,
    )

    def forbidden_upsert(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Invalid dry-run result must not upsert candidates.")

    def forbidden_state(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Dry-run must not update state.")

    monkeypatch.setattr(repository, "upsert_candidate", forbidden_upsert)
    monkeypatch.setattr(repository, "update_state", forbidden_state)
    result = ContactDiscoveryService(
        repository,
        FakeProvider(
            provider_result(
                candidate(company.id, email="valid@example.com"),
                candidate(company.id, email=None, source_url=None),
            )
        ),
    ).run(company_id=company.id, website_url="https://example.com", dry_run=True)
    session.commit()
    session.refresh(state)
    session.refresh(existing_row)

    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_invalid_result",)
    assert result.candidates == ()
    assert result.candidate_upserts == 0
    assert result.state_persisted is False
    assert (
        state.provider,
        state.discovery_status,
        state.checked_at,
        state.last_error,
        state.updated_at,
    ) == state_snapshot
    assert (
        existing_row.name,
        existing_row.confidence,
        existing_row.updated_at,
    ) == candidate_snapshot


@pytest.mark.parametrize(
    "invalid_candidate",
    [
        ContactDiscoveryCandidateCreate(
            company_id=1,
            name="Name Only",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        ),
        ContactDiscoveryCandidateCreate(
            company_id=1,
            name="Unsafe Source",
            title="Director",
            source_url="javascript:alert(1)",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        ),
    ],
)
def test_repository_incompatible_candidate_identity_is_sanitized(
    session: Session, invalid_candidate: ContactDiscoveryCandidateCreate
) -> None:
    company = create_company(session)
    value = invalid_candidate.model_copy(update={"company_id": company.id})
    result = run(session, company, provider_result(value))
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_invalid_result",)
    assert result.candidates == ()
    assert "insufficient" not in repr(result).casefold()
    assert "javascript" not in repr(result).casefold()


@pytest.mark.parametrize(
    "valid_candidate",
    [
        ContactDiscoveryCandidateCreate(
            company_id=1,
            email="valid@example.com",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        ),
        ContactDiscoveryCandidateCreate(
            company_id=1,
            name="Fallback Person",
            title="Director",
            source_url="https://example.com/team?ref=nav#people",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        ),
    ],
)
def test_repository_compatible_email_and_fallback_identities_are_accepted(
    session: Session, valid_candidate: ContactDiscoveryCandidateCreate
) -> None:
    company = create_company(session)
    value = valid_candidate.model_copy(update={"company_id": company.id})
    result = run(session, company, provider_result(value))
    assert result.status == ContactDiscoveryStatus.SUCCEEDED
    assert result.candidates == (value,)


def test_unknown_errors_are_sanitized_deduplicated_in_first_seen_order(session: Session) -> None:
    company = create_company(session)
    raw = "https://secret.example body header cookie charset traceback"
    result = run(
        session,
        company,
        provider_result(
            errors=(
                "page_parse_failed",
                raw,
                "page_parse_failed",
                "another raw error",
                "secondary_page_fetch_failed",
            )
        ),
    )
    assert result.errors == (
        "page_parse_failed",
        "provider_failed",
        "secondary_page_fetch_failed",
    )
    assert raw not in repr(result)


def test_blank_website_does_not_call_provider_and_returns_fixed_failure(session: Session) -> None:
    company = create_company(session)
    provider = FakeProvider()
    result = service(session, provider).run(company_id=company.id, website_url="  ", dry_run=True)
    assert provider.calls == []
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_invalid_result",)


def test_dry_run_calls_provider_once_returns_candidates_and_writes_nothing(
    session: Session,
) -> None:
    company = create_company(session)
    session.commit()
    provider = FakeProvider(provider_result(candidate(company.id)))
    result = service(session, provider).run(
        company_id=company.id, website_url="https://example.com", dry_run=True
    )
    assert provider.calls == [(company.id, "https://example.com")]
    assert len(result.candidates) == 1
    assert result.candidate_upserts == 0
    assert result.state_persisted is False
    session.commit()
    assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0


def test_dry_run_preserves_existing_state_candidate_and_timestamps(session: Session) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    checked_at = datetime(2025, 1, 2, tzinfo=UTC)
    state = repository.update_state(
        company.id,
        provider="old-provider",
        discovery_status=ContactDiscoveryStatus.PARTIAL,
        checked_at=checked_at,
        last_error="page_parse_failed",
    )
    stored = repository.upsert_candidate(
        company.id, candidate(company.id, phone="123", confidence=80)
    )
    row = repository.get_candidate(stored.candidate.id)
    assert row is not None
    row.discovery_status = ContactDiscoveryCandidateStatus.REVIEWED
    session.commit()
    session.refresh(state)
    session.refresh(row)
    persisted_checked_at = state.checked_at
    state_updated_at = state.updated_at
    candidate_updated_at = row.updated_at

    result = run(
        session,
        company,
        provider_result(candidate(company.id, name="Replacement", phone="999", confidence=100)),
    )
    session.commit()
    session.refresh(state)
    session.refresh(row)
    assert result.candidate_upserts == 0
    assert state.provider == "old-provider"
    assert state.discovery_status == ContactDiscoveryStatus.PARTIAL
    assert state.checked_at == persisted_checked_at
    assert state.last_error == "page_parse_failed"
    assert state.updated_at == state_updated_at
    assert row.name == "Ada Lovelace"
    assert row.phone == "123"
    assert row.confidence == 80
    assert row.discovery_status == ContactDiscoveryCandidateStatus.REVIEWED
    assert row.updated_at == candidate_updated_at


def test_persist_success_is_idempotent_and_repository_owns_merge_rules(session: Session) -> None:
    company = create_company(session)
    first = run(
        session,
        company,
        provider_result(candidate(company.id, phone=None, confidence=60)),
        dry_run=False,
    )
    second = run(
        session,
        company,
        provider_result(
            candidate(
                company.id,
                name=None,
                title=None,
                phone="123",
                confidence=90,
            )
        ),
        dry_run=False,
    )
    third = run(
        session,
        company,
        provider_result(
            candidate(
                company.id,
                name=None,
                title=None,
                phone=None,
                confidence=10,
            )
        ),
        dry_run=False,
    )
    session.commit()
    state = ContactDiscoveryRepository(session).get_state_by_company_id(company.id)
    rows = ContactDiscoveryRepository(session).list_candidates_for_company(company.id)
    assert first.candidate_upserts == second.candidate_upserts == third.candidate_upserts == 1
    assert first.state_persisted is True
    assert state is not None
    assert state.discovery_status == ContactDiscoveryStatus.SUCCEEDED
    assert state.last_error is None
    assert len(rows) == 1
    assert rows[0].phone == "123"
    assert rows[0].name == "Ada Lovelace"
    assert rows[0].confidence == 90
    assert rows[0].discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED


def test_persist_partial_keeps_old_candidates_and_first_safe_error(session: Session) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    repository.upsert_candidate(company.id, candidate(company.id, email="old@example.com"))
    result = run(
        session,
        company,
        provider_result(
            candidate(company.id, email="new@example.com"),
            errors=("secondary_page_fetch_failed", "page_parse_failed"),
        ),
        dry_run=False,
    )
    state = repository.get_state_by_company_id(company.id)
    assert result.status == ContactDiscoveryStatus.PARTIAL
    assert result.candidate_upserts == 1
    assert state is not None
    assert state.discovery_status == ContactDiscoveryStatus.PARTIAL
    assert state.last_error == "secondary_page_fetch_failed"
    assert len(repository.list_candidates_for_company(company.id)) == 2


@pytest.mark.parametrize(
    ("result", "status", "last_error"),
    [
        (provider_result(), ContactDiscoveryStatus.NOT_FOUND, None),
        (
            provider_result(
                attempted_pages=1,
                successful_pages=0,
                errors=("homepage_fetch_failed",),
            ),
            ContactDiscoveryStatus.FAILED,
            "homepage_fetch_failed",
        ),
    ],
)
def test_not_found_and_failed_update_state_without_deleting_existing_candidates(
    session: Session,
    result: WebsiteContactDiscoveryProviderResult,
    status: ContactDiscoveryStatus,
    last_error: str | None,
) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    repository.upsert_candidate(company.id, candidate(company.id))
    run_result = run(session, company, result, dry_run=False)
    state = repository.get_state_by_company_id(company.id)
    assert run_result.candidate_upserts == 0
    assert state is not None
    assert state.discovery_status == status
    assert state.last_error == last_error
    assert len(repository.list_candidates_for_company(company.id)) == 1


def test_provider_exception_persists_only_fixed_failed_state(session: Session) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    repository.upsert_candidate(company.id, candidate(company.id))
    result = service(session, FakeProvider(error=RuntimeError("secret"))).run(
        company_id=company.id, website_url="https://example.com", dry_run=False
    )
    state = repository.get_state_by_company_id(company.id)
    assert result.candidates == ()
    assert result.candidate_upserts == 0
    assert state is not None
    assert state.discovery_status == ContactDiscoveryStatus.FAILED
    assert state.last_error == "provider_failed"
    assert len(repository.list_candidates_for_company(company.id)) == 1


@pytest.mark.parametrize(
    "protected_status",
    [
        ContactDiscoveryCandidateStatus.REVIEWED,
        ContactDiscoveryCandidateStatus.PROMOTED,
        ContactDiscoveryCandidateStatus.REJECTED,
    ],
)
def test_persist_preserves_protected_candidates(
    session: Session, protected_status: ContactDiscoveryCandidateStatus
) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    created = repository.upsert_candidate(
        company.id, candidate(company.id, name="Protected", phone="123", confidence=40)
    )
    stored = repository.get_candidate(created.candidate.id)
    assert stored is not None
    stored.discovery_status = protected_status
    session.flush()
    result = run(
        session,
        company,
        provider_result(candidate(company.id, name="Replacement", phone="999", confidence=100)),
        dry_run=False,
    )
    session.refresh(stored)
    assert result.candidate_upserts == 1
    assert stored.discovery_status == protected_status
    assert stored.name == "Protected"
    assert stored.phone == "123"
    assert stored.confidence == 40


def test_service_never_commits(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    company = create_company(session)

    def forbidden_commit() -> None:
        raise AssertionError("Service must not commit.")

    monkeypatch.setattr(session, "commit", forbidden_commit)
    result = run(session, company, provider_result(candidate(company.id)), dry_run=False)
    assert result.state_persisted is True


def test_database_failure_propagates_and_caller_rollback_removes_partial_work(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = create_company(session)
    session.commit()
    repository = ContactDiscoveryRepository(session)

    def fail_state(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("database failed")

    monkeypatch.setattr(repository, "update_state", fail_state)
    with pytest.raises(RuntimeError, match="database failed"):
        ContactDiscoveryService(
            repository, FakeProvider(provider_result(candidate(company.id)))
        ).run(company_id=company.id, website_url="https://example.com", dry_run=False)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 0
    assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 0


def test_second_upsert_failure_propagates_and_rollback_removes_first_flush(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = create_company(session)
    repository = ContactDiscoveryRepository(session)
    state = repository.update_state(
        company.id,
        provider="existing-provider",
        discovery_status=ContactDiscoveryStatus.NOT_FOUND,
        checked_at=datetime(2025, 3, 4, tzinfo=UTC),
        last_error=None,
    )
    session.commit()
    session.refresh(state)
    state_snapshot = (
        state.provider,
        state.discovery_status,
        state.checked_at,
        state.last_error,
    )
    original_upsert = repository.upsert_candidate
    calls = 0

    def fail_second_upsert(company_id: int, value: ContactDiscoveryCandidateCreate) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled database failure")
        return original_upsert(company_id, value)

    monkeypatch.setattr(repository, "upsert_candidate", fail_second_upsert)
    provider = FakeProvider(
        provider_result(
            candidate(company.id, email="first@example.com"),
            candidate(company.id, email="second@example.com"),
        )
    )
    with pytest.raises(RuntimeError, match="controlled database failure"):
        ContactDiscoveryService(repository, provider).run(
            company_id=company.id,
            website_url="https://example.com",
            dry_run=False,
        )
    assert calls == 2
    session.rollback()
    session.refresh(state)
    assert repository.list_candidates_for_company(company.id) == []
    assert (
        state.provider,
        state.discovery_status,
        state.checked_at,
        state.last_error,
    ) == state_snapshot


def test_successful_caller_commit_persists_state_and_candidate_atomically(session: Session) -> None:
    company = create_company(session)
    session.commit()
    result = run(session, company, provider_result(candidate(company.id)), dry_run=False)
    session.commit()
    assert result.state_persisted is True
    assert session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) == 1
    assert session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) == 1


def test_service_has_no_forbidden_boundaries_or_commit_calls() -> None:
    source = Path("app/modules/contact_discovery/service.py").read_text(encoding="utf-8").casefold()
    for forbidden in (
        "app.modules.contact.models",
        "app.modules.lead",
        "app.modules.company_enrichment",
        ".commit(",
        "delete(",
        "socket",
        "httpx",
        "requests",
        "serpapi",
        "selenium",
        "playwright",
        "scraping",
        "login",
        "send_email",
        "queue",
        "worker",
        "scheduler",
        "openai",
    ):
        assert forbidden not in source


def test_result_does_not_leak_transport_or_database_objects(session: Session) -> None:
    company = create_company(session)
    result = run(session, company, provider_result(candidate(company.id)))
    representation = repr(result).casefold()
    for marker in ("session", "headers", "cookies", "raw html", "traceback"):
        assert marker not in representation


def test_replacing_frozen_provider_result_with_invalid_runtime_values_is_rejected(
    session: Session,
) -> None:
    company = create_company(session)
    valid = provider_result(candidate(company.id))
    invalid = replace(valid, limited_link_scan=1)  # type: ignore[arg-type]
    result = run(session, company, invalid, dry_run=False)
    assert result.status == ContactDiscoveryStatus.FAILED
    assert result.errors == ("provider_invalid_result",)
    assert result.candidate_upserts == 0

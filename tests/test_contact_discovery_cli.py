from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import typer
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

import app.cli.contact_discovery as cli
from app.cli.main import app as main_app
from app.core.database.base import Base
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
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
    ContactDiscoveryProvider,
    ContactDiscoveryRunResult,
    ContactDiscoveryService,
)
from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProviderResult
from app.modules.project.models import Project

runner = CliRunner()


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as database_session:
        yield database_session


def create_company(*, website: str | None) -> tuple[int, int]:
    with SessionLocal() as session:
        project = Project(name="CLI Contact Discovery")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="CLI Company", website=website)
        session.add(company)
        session.commit()
        return project.id, company.id


def candidate(company_id: int, **values: object) -> ContactDiscoveryCandidateCreate:
    data: dict[str, object] = {
        "company_id": company_id,
        "name": "Ada Lovelace",
        "title": "Director",
        "email": "ada@example.com",
        "phone": "+1 212 555 0199",
        "source_url": "https://secret.example/team?token=secret",
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 80,
    }
    data.update(values)
    return ContactDiscoveryCandidateCreate(**data)


def result(
    company_id: int,
    *,
    status: ContactDiscoveryStatus = ContactDiscoveryStatus.SUCCEEDED,
    dry_run: bool = True,
    candidates: tuple[ContactDiscoveryCandidateCreate, ...] = (),
    errors: tuple[str, ...] = (),
    candidate_upserts: int = 0,
    state_persisted: bool = False,
) -> ContactDiscoveryRunResult:
    return ContactDiscoveryRunResult(
        company_id=company_id,
        dry_run=dry_run,
        status=status,
        candidates=candidates,
        attempted_pages=3,
        successful_pages=2,
        errors=errors,
        candidate_upserts=candidate_upserts,
        state_persisted=state_persisted,
        selected_urls=2,
        limited_link_scan=True,
    )


class RecordingProvider:
    provider_name = "fake"

    def discover(
        self,
        *,
        company_id: int,
        website_url: str,
    ) -> WebsiteContactDiscoveryProviderResult:
        return WebsiteContactDiscoveryProviderResult(
            attempted_pages=1,
            successful_pages=1,
        )


def recording_provider_factory() -> ContactDiscoveryProvider:
    return RecordingProvider()


class StaticProvider:
    provider_name = "fake"

    def __init__(self, provider_result: WebsiteContactDiscoveryProviderResult) -> None:
        self.provider_result = provider_result

    def discover(
        self,
        *,
        company_id: int,
        website_url: str,
    ) -> WebsiteContactDiscoveryProviderResult:
        return self.provider_result


_TEST_UNSET = object()


class FailingAfterStateRepository(ContactDiscoveryRepository):
    def update_state(
        self,
        company_id: int,
        *,
        provider: object = _TEST_UNSET,
        discovery_status: object = _TEST_UNSET,
        checked_at: object = _TEST_UNSET,
        last_error: object = _TEST_UNSET,
    ) -> CompanyContactDiscoveryState:
        super().update_state(
            company_id,
            provider=provider,
            discovery_status=discovery_status,
            checked_at=checked_at,
            last_error=last_error,
        )
        raise RuntimeError("ATOMIC_ROLLBACK_SECRET_SQL")


def isolated_session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'contact-discovery.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )


class FakeService:
    def __init__(
        self,
        run_result: ContactDiscoveryRunResult,
        *,
        error: BaseException | None = None,
        before_return: Callable[[], None] | None = None,
    ) -> None:
        self.run_result = run_result
        self.error = error
        self.before_return = before_return
        self.calls: list[tuple[int, str, bool]] = []

    def run(self, *, company_id: int, website_url: str, dry_run: bool) -> ContactDiscoveryRunResult:
        self.calls.append((company_id, website_url, dry_run))
        if self.before_return is not None:
            self.before_return()
        if self.error is not None:
            raise self.error
        return self.run_result


def factory_for(fake: FakeService) -> cli.ServiceFactory:
    def make_service(
        _repository: ContactDiscoveryRepository, _provider: ContactDiscoveryProvider
    ) -> ContactDiscoveryService:
        return cast(ContactDiscoveryService, fake)

    return make_service


def counts() -> tuple[int, int]:
    with SessionLocal() as session:
        states = session.scalar(select(func.count()).select_from(CompanyContactDiscoveryState)) or 0
        candidates = (
            session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate)) or 0
        )
        return states, candidates


def test_command_is_registered_with_required_safe_options_only() -> None:
    root = runner.invoke(main_app, ["--help"])
    command = runner.invoke(main_app, ["contact-discovery", "run", "--help"])
    assert root.exit_code == command.exit_code == 0
    assert "contact-discovery" in root.output
    for option in ("--company-id", "--persist", "--yes"):
        assert option in command.output
    for forbidden in ("--website", "--limit", "--status", "--project-id", "--recent"):
        assert forbidden not in command.output


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--company-id", "0"],
        ["--company-id", "-1"],
        ["--company-id", "1.5"],
        ["--company-id", "invalid"],
        ["--company-id", "1", "--company-id", "2"],
    ],
)
def test_parser_rejects_missing_or_invalid_company_id_before_provider(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    def forbidden_provider() -> None:
        raise AssertionError("Provider must not be constructed for invalid CLI input.")

    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", forbidden_provider)
    response = runner.invoke(cli.app, ["run", *arguments])
    assert response.exit_code == 2
    assert counts() == (0, 0)


def test_missing_company_is_fixed_safe_failure_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    def provider_factory() -> RecordingProvider:
        nonlocal constructed
        constructed = True
        return RecordingProvider()

    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", provider_factory)
    response = runner.invoke(cli.app, ["run", "--company-id", "999"])
    assert response.exit_code == 1
    assert response.output.strip() == "company_not_found"
    assert constructed is False
    assert counts() == (0, 0)


@pytest.mark.parametrize("website", [None, "", "   "])
def test_missing_website_is_fixed_safe_failure_without_provider_or_mutation(
    monkeypatch: pytest.MonkeyPatch, website: str | None
) -> None:
    _, company_id = create_company(website=website)

    def forbidden_provider() -> None:
        raise AssertionError("Provider must not be constructed without a website.")

    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", forbidden_provider)
    response = runner.invoke(cli.app, ["run", "--company-id", str(company_id)])
    assert response.exit_code == 1
    assert response.output.strip() == "company_website_missing"
    assert counts() == (0, 0)
    with SessionLocal() as session:
        company = session.get(Company, company_id)
        assert company is not None
        assert company.website == website


def test_persist_without_yes_refuses_before_session_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> object:
        raise AssertionError("Confirmation refusal must happen before dependencies.")

    monkeypatch.setattr(cli, "SessionLocal", forbidden)
    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", forbidden)
    response = runner.invoke(cli.app, ["run", "--company-id", "1", "--persist"])
    assert response.exit_code == 1
    assert response.output.strip() == "persist_confirmation_required"


@pytest.mark.parametrize("extra", [[], ["--yes"]])
def test_default_and_yes_only_are_dry_run(extra: list[str]) -> None:
    _, company_id = create_company(website=" https://stored.example/path ")
    fake = FakeService(result(company_id))
    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=False,
        yes="--yes" in extra,
        provider_factory=recording_provider_factory,
        service_factory=factory_for(fake),
    )
    assert outcome.exit_code == 0
    assert fake.calls == [(company_id, " https://stored.example/path ", True)]
    assert counts() == (0, 0)


def test_dry_run_rolls_back_defensively_and_never_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    commits = 0
    rollbacks = 0
    original_commit = database_session.commit
    original_rollback = database_session.rollback

    def track_commit() -> None:
        nonlocal commits
        commits += 1
        original_commit()

    def track_rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        original_rollback()

    monkeypatch.setattr(database_session, "commit", track_commit)
    monkeypatch.setattr(database_session, "rollback", track_rollback)
    fake = FakeService(result(company_id))
    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=False,
        yes=False,
        session_factory=lambda: database_session,
        provider_factory=recording_provider_factory,
        service_factory=factory_for(fake),
    )
    assert outcome.exit_code == 0
    assert commits == 0
    assert rollbacks == 1
    assert fake.calls == [(company_id, "https://stored.example", True)]


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (ContactDiscoveryStatus.SUCCEEDED, 0),
        (ContactDiscoveryStatus.PARTIAL, 0),
        (ContactDiscoveryStatus.NOT_FOUND, 0),
        (ContactDiscoveryStatus.FAILED, 1),
    ],
)
def test_dry_run_exit_codes(status: ContactDiscoveryStatus, exit_code: int) -> None:
    _, company_id = create_company(website="https://stored.example")
    fake = FakeService(result(company_id, status=status))
    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=False,
        yes=False,
        provider_factory=recording_provider_factory,
        service_factory=factory_for(fake),
    )
    assert outcome.exit_code == exit_code


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (ContactDiscoveryStatus.SUCCEEDED, 0),
        (ContactDiscoveryStatus.PARTIAL, 0),
        (ContactDiscoveryStatus.NOT_FOUND, 0),
        (ContactDiscoveryStatus.FAILED, 1),
    ],
)
def test_confirmed_persist_commits_exactly_once_after_service(
    monkeypatch: pytest.MonkeyPatch,
    status: ContactDiscoveryStatus,
    exit_code: int,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    events: list[str] = []
    original_commit = database_session.commit

    def track_commit() -> None:
        events.append("commit")
        original_commit()

    monkeypatch.setattr(database_session, "commit", track_commit)
    fake = FakeService(
        result(
            company_id,
            status=status,
            dry_run=False,
            candidate_upserts=1,
            state_persisted=True,
        ),
        before_return=lambda: events.append("service"),
    )
    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=True,
        yes=True,
        session_factory=lambda: database_session,
        provider_factory=recording_provider_factory,
        service_factory=factory_for(fake),
    )
    assert outcome.exit_code == exit_code
    assert fake.calls == [(company_id, "https://stored.example", False)]
    assert events == ["service", "commit"]


def test_service_exception_rolls_back_real_flushed_state_and_is_sanitized() -> None:
    _, company_id = create_company(website="https://stored.example")
    captured_repository: ContactDiscoveryRepository | None = None

    def make_service(
        repository: ContactDiscoveryRepository, _provider: object
    ) -> ContactDiscoveryService:
        nonlocal captured_repository
        captured_repository = repository

        def flush_then_fail() -> None:
            repository.update_state(
                company_id,
                discovery_status=ContactDiscoveryStatus.SUCCEEDED,
            )

        return cast(
            ContactDiscoveryService,
            FakeService(
                result(company_id, dry_run=False),
                error=RuntimeError(
                    "sqlite:///secret.db https://secret.example <html> headers cookies traceback"
                ),
                before_return=flush_then_fail,
            ),
        )

    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=True,
        yes=True,
        provider_factory=recording_provider_factory,
        service_factory=make_service,
    )
    assert captured_repository is not None
    assert outcome == cli.ContactDiscoveryCommandOutcome(
        exit_code=1,
        error_code="contact_discovery_execution_failed",
    )
    assert counts() == (0, 0)
    assert "secret" not in repr(outcome)


def test_commit_exception_rolls_back_and_does_not_retry_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    commits = 0
    rollbacks = 0
    original_rollback = database_session.rollback

    def fail_commit() -> None:
        nonlocal commits
        commits += 1
        raise RuntimeError("SQL secret commit failure")

    def track_rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        original_rollback()

    monkeypatch.setattr(database_session, "commit", fail_commit)
    monkeypatch.setattr(database_session, "rollback", track_rollback)
    fake = FakeService(result(company_id, dry_run=False, state_persisted=True))
    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=True,
        yes=True,
        session_factory=lambda: database_session,
        provider_factory=recording_provider_factory,
        service_factory=factory_for(fake),
    )
    assert outcome.error_code == "contact_discovery_execution_failed"
    assert commits == rollbacks == 1
    assert counts() == (0, 0)


def test_service_and_rollback_exceptions_emit_only_fixed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    commits = 0
    rollbacks = 0

    def track_commit() -> None:
        nonlocal commits
        commits += 1

    def fail_rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        raise RuntimeError("ROLLBACK_SECRET_DB_PATH")

    fake = FakeService(
        result(company_id),
        error=RuntimeError("SERVICE_SECRET_SQL https://secret.example traceback"),
    )
    monkeypatch.setattr(cli, "SessionLocal", lambda: database_session)
    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", recording_provider_factory)
    monkeypatch.setattr(cli, "ContactDiscoveryService", factory_for(fake))
    monkeypatch.setattr(database_session, "commit", track_commit)
    monkeypatch.setattr(database_session, "rollback", fail_rollback)

    response = runner.invoke(cli.app, ["run", "--company-id", str(company_id)])

    assert response.exit_code == 1
    assert response.output.strip() == "contact_discovery_execution_failed"
    assert commits == 0
    assert rollbacks == 1


def test_commit_and_rollback_exceptions_emit_only_fixed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    commits = 0
    rollbacks = 0

    def fail_commit() -> None:
        nonlocal commits
        commits += 1
        raise RuntimeError("COMMIT_SECRET_SQL")

    def fail_rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        raise RuntimeError("ROLLBACK_SECRET_DB_PATH")

    fake = FakeService(result(company_id, dry_run=False, state_persisted=True))
    monkeypatch.setattr(cli, "SessionLocal", lambda: database_session)
    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", recording_provider_factory)
    monkeypatch.setattr(cli, "ContactDiscoveryService", factory_for(fake))
    monkeypatch.setattr(database_session, "commit", fail_commit)
    monkeypatch.setattr(database_session, "rollback", fail_rollback)

    response = runner.invoke(
        cli.app,
        ["run", "--company-id", str(company_id), "--persist", "--yes"],
    )

    assert response.exit_code == 1
    assert response.output.strip() == "contact_discovery_execution_failed"
    assert commits == 1
    assert rollbacks == 1


def test_normal_dry_run_rollback_exception_suppresses_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    commits = 0
    rollbacks = 0

    def track_commit() -> None:
        nonlocal commits
        commits += 1

    def fail_rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        raise RuntimeError("ROLLBACK_SECRET_DB_PATH")

    fake = FakeService(result(company_id))
    monkeypatch.setattr(cli, "SessionLocal", lambda: database_session)
    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", recording_provider_factory)
    monkeypatch.setattr(cli, "ContactDiscoveryService", factory_for(fake))
    monkeypatch.setattr(database_session, "commit", track_commit)
    monkeypatch.setattr(database_session, "rollback", fail_rollback)

    response = runner.invoke(cli.app, ["run", "--company-id", str(company_id)])

    assert response.exit_code == 1
    assert response.output.strip() == "contact_discovery_execution_failed"
    assert "Contact discovery" not in response.output
    assert commits == 0
    assert rollbacks == 1


def test_missing_company_rollback_exception_replaces_local_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_session = SessionLocal()
    rollbacks = 0

    def fail_rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1
        raise RuntimeError("ROLLBACK_SECRET_DB_PATH")

    def forbidden_provider() -> ContactDiscoveryProvider:
        raise AssertionError("Provider must not be created for a missing company.")

    monkeypatch.setattr(cli, "SessionLocal", lambda: database_session)
    monkeypatch.setattr(cli, "WebsiteContactDiscoveryProvider", forbidden_provider)
    monkeypatch.setattr(database_session, "rollback", fail_rollback)

    response = runner.invoke(cli.app, ["run", "--company-id", "999999"])

    assert response.exit_code == 1
    assert response.output.strip() == "contact_discovery_execution_failed"
    assert "company_not_found" not in response.output
    assert rollbacks == 1


def test_real_sqlite_persist_commits_candidate_and_state_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    make_session = isolated_session_factory(tmp_path)
    with make_session() as setup_session:
        project = Project(name="F6E persist integration")
        setup_session.add(project)
        setup_session.flush()
        company = Company(
            project_id=project.id,
            name="Persist Company",
            website="https://stored.example",
        )
        setup_session.add(company)
        setup_session.commit()
        company_id = company.id
        original_company = (company.name, company.website, company.status)

    provider_result = WebsiteContactDiscoveryProviderResult(
        candidates=(candidate(company_id),),
        attempted_pages=1,
        successful_pages=1,
    )
    cli_session = make_session()
    commits = 0
    original_commit = cli_session.commit

    def track_commit() -> None:
        nonlocal commits
        commits += 1
        original_commit()

    monkeypatch.setattr(cli_session, "commit", track_commit)
    monkeypatch.setattr(cli, "SessionLocal", lambda: cli_session)
    monkeypatch.setattr(
        cli, "WebsiteContactDiscoveryProvider", lambda: StaticProvider(provider_result)
    )

    response = runner.invoke(
        cli.app,
        ["run", "--company-id", str(company_id), "--persist", "--yes"],
    )

    assert response.exit_code == 0
    assert "Status: SUCCEEDED" in response.output
    assert commits == 1
    with make_session() as inspection_session:
        states = list(inspection_session.scalars(select(CompanyContactDiscoveryState)))
        candidates = list(inspection_session.scalars(select(ContactDiscoveryCandidate)))
        persisted_company = inspection_session.get(Company, company_id)
        assert len(states) == len(candidates) == 1
        assert states[0].company_id == company_id
        assert states[0].discovery_status == ContactDiscoveryStatus.SUCCEEDED
        assert states[0].last_error is None
        assert candidates[0].company_id == company_id
        assert persisted_company is not None
        assert (
            persisted_company.name,
            persisted_company.website,
            persisted_company.status,
        ) == original_company
        assert inspection_session.scalar(select(func.count()).select_from(Contact)) == 0


def test_real_sqlite_dry_run_preserves_existing_state_and_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    make_session = isolated_session_factory(tmp_path)
    checked_at = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)
    with make_session() as setup_session:
        project = Project(name="F6E dry-run integration")
        setup_session.add(project)
        setup_session.flush()
        company = Company(
            project_id=project.id,
            name="Dry Run Company",
            website="https://stored.example",
        )
        setup_session.add(company)
        setup_session.flush()
        company_id = company.id
        repository = ContactDiscoveryRepository(setup_session)
        repository.update_state(
            company_id,
            provider="existing",
            discovery_status=ContactDiscoveryStatus.PARTIAL,
            checked_at=checked_at,
            last_error="secondary_page_fetch_failed",
        )
        repository.upsert_candidate(
            company_id,
            candidate(company_id, title="Existing title", confidence=40),
        )
        setup_session.commit()
        original_company = (company.name, company.website, company.status)

    provider_result = WebsiteContactDiscoveryProviderResult(
        candidates=(candidate(company_id, title="Changed title", confidence=95),),
        attempted_pages=1,
        successful_pages=1,
    )
    cli_session = make_session()
    monkeypatch.setattr(cli, "SessionLocal", lambda: cli_session)
    monkeypatch.setattr(
        cli, "WebsiteContactDiscoveryProvider", lambda: StaticProvider(provider_result)
    )

    response = runner.invoke(cli.app, ["run", "--company-id", str(company_id)])

    assert response.exit_code == 0
    assert "Mode: DRY_RUN" in response.output
    assert "Status: SUCCEEDED" in response.output
    with make_session() as inspection_session:
        states = list(inspection_session.scalars(select(CompanyContactDiscoveryState)))
        candidates = list(inspection_session.scalars(select(ContactDiscoveryCandidate)))
        persisted_company = inspection_session.get(Company, company_id)
        assert len(states) == len(candidates) == 1
        assert states[0].discovery_status == ContactDiscoveryStatus.PARTIAL
        assert states[0].last_error == "secondary_page_fetch_failed"
        assert states[0].checked_at == checked_at.replace(tzinfo=None)
        assert candidates[0].title == "Existing title"
        assert candidates[0].confidence == 40
        assert candidates[0].discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED
        assert persisted_company is not None
        assert (
            persisted_company.name,
            persisted_company.website,
            persisted_company.status,
        ) == original_company
        assert inspection_session.scalar(select(func.count()).select_from(Contact)) == 0


def test_real_sqlite_persist_exception_rolls_back_candidate_and_state_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    make_session = isolated_session_factory(tmp_path)
    with make_session() as setup_session:
        project = Project(name="F6E rollback integration")
        setup_session.add(project)
        setup_session.flush()
        company = Company(
            project_id=project.id,
            name="Rollback Company",
            website="https://stored.example",
        )
        setup_session.add(company)
        setup_session.commit()
        company_id = company.id
        original_company = (company.name, company.website, company.status)

    provider_result = WebsiteContactDiscoveryProviderResult(
        candidates=(candidate(company_id),),
        attempted_pages=1,
        successful_pages=1,
    )
    cli_session = make_session()
    commits = 0
    original_commit = cli_session.commit

    def track_commit() -> None:
        nonlocal commits
        commits += 1
        original_commit()

    def make_failing_service(
        repository: ContactDiscoveryRepository,
        provider: ContactDiscoveryProvider,
    ) -> ContactDiscoveryService:
        return ContactDiscoveryService(FailingAfterStateRepository(repository.session), provider)

    monkeypatch.setattr(cli_session, "commit", track_commit)
    monkeypatch.setattr(cli, "SessionLocal", lambda: cli_session)
    monkeypatch.setattr(
        cli, "WebsiteContactDiscoveryProvider", lambda: StaticProvider(provider_result)
    )
    monkeypatch.setattr(cli, "ContactDiscoveryService", make_failing_service)

    response = runner.invoke(
        cli.app,
        ["run", "--company-id", str(company_id), "--persist", "--yes"],
    )

    assert response.exit_code == 1
    assert response.output.strip() == "contact_discovery_execution_failed"
    assert commits == 0
    with make_session() as inspection_session:
        assert (
            inspection_session.scalar(
                select(func.count()).select_from(CompanyContactDiscoveryState)
            )
            == 0
        )
        assert (
            inspection_session.scalar(select(func.count()).select_from(ContactDiscoveryCandidate))
            == 0
        )
        persisted_company = inspection_session.get(Company, company_id)
        assert persisted_company is not None
        assert (
            persisted_company.name,
            persisted_company.website,
            persisted_company.status,
        ) == original_company
        assert inspection_session.scalar(select(func.count()).select_from(Contact)) == 0


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(2)])
def test_base_exceptions_are_not_converted(error: BaseException) -> None:
    _, company_id = create_company(website="https://stored.example")
    fake = FakeService(result(company_id), error=error)
    with pytest.raises(type(error)):
        cli.execute_contact_discovery(
            company_id=company_id,
            persist=False,
            yes=False,
            provider_factory=recording_provider_factory,
            service_factory=factory_for(fake),
        )
    assert counts() == (0, 0)


def test_close_exception_is_sanitized_without_replacing_normal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    fake = FakeService(result(company_id))

    def fail_close() -> None:
        raise RuntimeError("CLOSE_SECRET_DB_PATH")

    monkeypatch.setattr(database_session, "close", fail_close)
    outcome = cli.execute_contact_discovery(
        company_id=company_id,
        persist=False,
        yes=False,
        session_factory=lambda: database_session,
        provider_factory=recording_provider_factory,
        service_factory=factory_for(fake),
    )

    assert outcome == cli.ContactDiscoveryCommandOutcome(
        exit_code=1,
        error_code="contact_discovery_execution_failed",
    )


def test_close_exception_does_not_replace_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, company_id = create_company(website="https://stored.example")
    database_session = SessionLocal()
    fake = FakeService(result(company_id), error=KeyboardInterrupt())

    def fail_close() -> None:
        raise RuntimeError("CLOSE_SECRET_DB_PATH")

    monkeypatch.setattr(database_session, "close", fail_close)
    with pytest.raises(KeyboardInterrupt):
        cli.execute_contact_discovery(
            company_id=company_id,
            persist=False,
            yes=False,
            session_factory=lambda: database_session,
            provider_factory=recording_provider_factory,
            service_factory=factory_for(fake),
        )


def test_deterministic_safe_summary_omits_urls_and_bounds_candidate_output() -> None:
    candidates = tuple(
        candidate(
            7,
            name=f"Person\x1b[{index}",
            title=None,
            email=None,
            phone=None,
        )
        for index in range(102)
    )
    run_result = result(
        7,
        candidates=candidates,
        errors=("secondary_page_fetch_failed", "unsafe\x1b[31m\nerror"),
    )
    command = runner.invoke(
        cli.app,
        ["run", "--company-id", "7"],
    )
    assert command.exit_code == 1  # no saved company; direct printer is exercised below
    from io import StringIO

    stream = StringIO()
    monkeypatch_context = pytest.MonkeyPatch()
    monkeypatch_context.setattr(typer, "echo", lambda value: stream.write(f"{value}\n"))
    try:
        cli._print_result(run_result)
    finally:
        monkeypatch_context.undo()
    output = stream.getvalue()
    for expected in (
        "Contact discovery",
        "Company ID: 7",
        "Mode: DRY_RUN",
        "Status: SUCCEEDED",
        "Candidates: 102",
        "Attempted pages: 3",
        "Successful pages: 2",
        "Selected secondary URLs: 2",
        "Candidate upserts: 0",
        "State persisted: no",
        "Limited link scan: yes",
        "Errors: secondary_page_fetch_failed",
        "Omitted candidates: 2",
    ):
        assert expected in output
    assert output.count("Candidate ") == 101  # summary field plus 100 rows
    for unsafe in ("source_url", "secret.example", "token=secret", "\x1b", "<html>"):
        assert unsafe not in output
    assert "unsafe [31m error" in output


def test_no_errors_and_missing_candidate_values_have_stable_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_result = result(1, candidates=(candidate(1, name=None, title=None, phone=None),))
    lines: list[str] = []
    monkeypatch.setattr(typer, "echo", lambda value: lines.append(str(value)))
    cli._print_result(run_result)
    assert "Errors: none" in lines
    assert "  Name: -" in lines
    assert "  Title: -" in lines
    assert "  Phone: -" in lines


def test_cli_source_preserves_stage_boundaries_and_lazy_provider_wiring() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "Contact(",
        "Lead(",
        "CompanyEnrichment(",
        "SerpApi",
        "serpapi",
        "selenium",
        "playwright",
        "send_email",
        "worker",
        "scheduler",
        "openai",
        "source_url)",
        "website_url)",
    ):
        assert forbidden not in source
    assert "WebsiteContactDiscoveryProvider" in source
    assert "ContactDiscoveryService" in source
    assert "for company" not in source.casefold()

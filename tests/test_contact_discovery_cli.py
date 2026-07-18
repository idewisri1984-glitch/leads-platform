from collections.abc import Callable, Generator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import app.cli.contact_discovery as cli
from app.cli.main import app as main_app
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
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
        provider_factory=RecordingProvider,
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
        provider_factory=RecordingProvider,
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
        provider_factory=RecordingProvider,
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
        provider_factory=RecordingProvider,
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
        provider_factory=RecordingProvider,
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
        provider_factory=RecordingProvider,
        service_factory=factory_for(fake),
    )
    assert outcome.error_code == "contact_discovery_execution_failed"
    assert commits == rollbacks == 1
    assert counts() == (0, 0)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(2)])
def test_base_exceptions_are_not_converted(error: BaseException) -> None:
    _, company_id = create_company(website="https://stored.example")
    fake = FakeService(result(company_id), error=error)
    with pytest.raises(type(error)):
        cli.execute_contact_discovery(
            company_id=company_id,
            persist=False,
            yes=False,
            provider_factory=RecordingProvider,
            service_factory=factory_for(fake),
        )
    assert counts() == (0, 0)


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
    monkeypatch_context.setattr(cli.typer, "echo", lambda value: stream.write(f"{value}\n"))
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
    monkeypatch.setattr(cli.typer, "echo", lambda value: lines.append(str(value)))
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

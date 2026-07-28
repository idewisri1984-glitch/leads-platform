from types import SimpleNamespace
from typing import cast

import pytest
from typer.testing import CliRunner

from app.cli import contact_discovery_candidates as candidate_cli
from app.cli.main import app
from app.modules.contact.repository import ContactRepository
from app.modules.contact_discovery import (
    ContactDiscoveryCandidateNotEligibleError,
    ContactDiscoveryCandidatePromotionConsistencyError,
    ContactDiscoveryCandidatePromotionInvalidDataError,
    ContactDiscoveryCandidatePromotionNotFoundError,
    ContactDiscoveryCandidatePromotionResult,
    ContactDiscoveryCandidateStatus,
)

runner = CliRunner()


def promotion_result(
    *, created: bool = True, changed: bool = True
) -> ContactDiscoveryCandidatePromotionResult:
    return ContactDiscoveryCandidatePromotionResult(
        candidate_id=7,
        company_id=3,
        contact_id=11,
        previous_status=(
            ContactDiscoveryCandidateStatus.REVIEWED
            if changed
            else ContactDiscoveryCandidateStatus.PROMOTED
        ),
        current_status=ContactDiscoveryCandidateStatus.PROMOTED,
        created_contact=created,
        changed=changed,
    )


class StrictSession:
    def __init__(
        self,
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class StrictPromotionService:
    def __init__(
        self,
        result: ContactDiscoveryCandidatePromotionResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or promotion_result()
        self.error = error
        self.calls: list[tuple[int, int]] = []

    def promote(
        self, company_id: int, candidate_id: int
    ) -> ContactDiscoveryCandidatePromotionResult:
        self.calls.append((company_id, candidate_id))
        if self.error is not None:
            raise self.error
        return self.result


def execute_with(
    service: StrictPromotionService,
    *,
    session: StrictSession | None = None,
    calls: list[str] | None = None,
) -> tuple[candidate_cli.CandidatePromotionCommandOutcome, StrictSession, list[str]]:
    value = session or StrictSession()
    call_log = calls if calls is not None else []
    staging = SimpleNamespace(role="staging")
    contacts = SimpleNamespace(role="contacts")

    def staging_factory(_session: object) -> object:
        call_log.append("staging")
        return staging

    def contact_factory(_session: object) -> object:
        call_log.append("contact")
        return contacts

    def service_factory(received_staging: object, received_contacts: object) -> object:
        assert received_staging is staging
        assert received_contacts is contacts
        call_log.append("service")
        return service

    outcome = candidate_cli.execute_promote_candidate(
        company_id=3,
        candidate_id=7,
        yes=True,
        session_factory=cast(candidate_cli.SessionFactory, lambda: value),
        staging_repository_factory=cast(candidate_cli.RepositoryFactory, staging_factory),
        contact_repository_factory=cast(candidate_cli.ContactRepositoryFactory, contact_factory),
        promotion_service_factory=cast(candidate_cli.PromotionServiceFactory, service_factory),
    )
    return outcome, value, call_log


def test_help_registers_promote_and_preserves_existing_commands() -> None:
    root = runner.invoke(app, ["contact-discovery", "--help"])
    result = runner.invoke(app, ["contact-discovery", "candidate", "--help"])
    promote = runner.invoke(app, ["contact-discovery", "candidate", "promote", "--help"])
    assert root.exit_code == result.exit_code == promote.exit_code == 0
    assert "run" in root.output
    assert all(
        command in result.output for command in ("list", "show", "review", "reject", "promote")
    )
    assert all(option in promote.output for option in ("--company-id", "--candidate-id", "--yes"))


def test_confirmation_is_required_before_any_dependency() -> None:
    calls: list[str] = []
    outcome = candidate_cli.execute_promote_candidate(
        company_id=3,
        candidate_id=7,
        yes=False,
        session_factory=cast(candidate_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome == candidate_cli.CandidatePromotionCommandOutcome(
        1, error_message="Candidate promotion requires --yes."
    )
    assert calls == []


@pytest.mark.parametrize("value", [0, -1, True, "3", 3.0, None, object()])
@pytest.mark.parametrize("field", ["company_id", "candidate_id"])
def test_invalid_direct_ids_create_no_session(field: str, value: object) -> None:
    calls: list[str] = []
    values: dict[str, object] = {"company_id": 3, "candidate_id": 7}
    values[field] = value
    outcome = candidate_cli.execute_promote_candidate(
        company_id=values["company_id"],  # type: ignore[arg-type]
        candidate_id=values["candidate_id"],  # type: ignore[arg-type]
        yes=True,
        session_factory=cast(candidate_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome.error_message == "Candidate promotion data is invalid."
    assert calls == []


def test_session_factory_failure_is_sanitized() -> None:
    def fail() -> object:
        raise RuntimeError("secret database path")

    outcome = candidate_cli.execute_promote_candidate(
        company_id=3,
        candidate_id=7,
        yes=True,
        session_factory=cast(candidate_cli.SessionFactory, fail),
    )
    assert outcome.error_message == "Candidate promotion failed."


@pytest.mark.parametrize(
    "result",
    [
        promotion_result(created=True, changed=True),
        promotion_result(created=False, changed=True),
        promotion_result(created=False, changed=False),
    ],
)
def test_success_paths_wire_factories_commit_and_close_once(
    result: ContactDiscoveryCandidatePromotionResult,
) -> None:
    service = StrictPromotionService(result=result)
    outcome, session, calls = execute_with(service)
    assert calls == ["staging", "contact", "service"]
    assert service.calls == [(3, 7)]
    assert outcome.result == result
    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert session.close_calls == 1


@pytest.mark.parametrize(
    ("result", "created", "changed", "previous"),
    [
        (promotion_result(created=True, changed=True), "yes", "yes", "REVIEWED"),
        (promotion_result(created=False, changed=True), "no", "yes", "REVIEWED"),
        (promotion_result(created=False, changed=False), "no", "no", "PROMOTED"),
    ],
)
def test_command_output_is_exact_and_non_pii(
    monkeypatch: pytest.MonkeyPatch,
    result: ContactDiscoveryCandidatePromotionResult,
    created: str,
    changed: str,
    previous: str,
) -> None:
    session = StrictSession()
    service = StrictPromotionService(result=result)
    monkeypatch.setattr(candidate_cli, "SessionLocal", lambda: session)
    monkeypatch.setattr(candidate_cli, "ContactDiscoveryRepository", lambda _session: object())
    monkeypatch.setattr(candidate_cli, "ContactRepository", lambda _session: object())
    monkeypatch.setattr(
        candidate_cli,
        "ContactDiscoveryCandidatePromotionService",
        lambda _staging, _contacts: service,
    )
    command = runner.invoke(
        app,
        [
            "contact-discovery",
            "candidate",
            "promote",
            "--company-id",
            "3",
            "--candidate-id",
            "7",
            "--yes",
        ],
    )
    assert command.exit_code == 0
    assert command.output.splitlines() == [
        "Candidate ID: 7",
        "Company ID: 3",
        "Contact ID: 11",
        f"Previous Status: {previous}",
        "Current Status: PROMOTED",
        f"Contact Created: {created}",
        f"Changed: {changed}",
    ]
    for forbidden in (
        "name",
        "email",
        "phone",
        "title",
        "source",
        "external",
        "notes",
        "error",
        "sql",
        "traceback",
    ):
        assert forbidden not in command.output.casefold()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ContactDiscoveryCandidatePromotionNotFoundError("private"), "Candidate was not found."),
        (
            ContactDiscoveryCandidateNotEligibleError("private"),
            "Candidate is not eligible for promotion.",
        ),
        (
            ContactDiscoveryCandidatePromotionInvalidDataError("private"),
            "Candidate promotion data is invalid.",
        ),
        (
            ContactDiscoveryCandidatePromotionConsistencyError("private"),
            "Candidate promotion state is inconsistent.",
        ),
        (RuntimeError("private database path"), "Candidate promotion failed."),
    ],
)
def test_failures_are_sanitized_rollback_and_close_once(
    error: Exception,
    message: str,
) -> None:
    outcome, session, _ = execute_with(StrictPromotionService(error=error))
    assert outcome.error_message == message
    assert "private" not in message
    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.parametrize("failing_factory", ["staging", "contact", "service"])
def test_dependency_factory_failures_are_generic(failing_factory: str) -> None:
    session = StrictSession()
    calls: list[str] = []

    def factory(name: str) -> object:
        calls.append(name)
        if name == failing_factory:
            raise RuntimeError("factory secret")
        return object()

    outcome = candidate_cli.execute_promote_candidate(
        company_id=3,
        candidate_id=7,
        yes=True,
        session_factory=cast(candidate_cli.SessionFactory, lambda: session),
        staging_repository_factory=cast(
            candidate_cli.RepositoryFactory, lambda _session: factory("staging")
        ),
        contact_repository_factory=cast(
            candidate_cli.ContactRepositoryFactory,
            lambda _session: factory("contact"),
        ),
        promotion_service_factory=cast(
            candidate_cli.PromotionServiceFactory,
            lambda _staging, _contact: factory("service"),
        ),
    )
    assert outcome.error_message == "Candidate promotion failed."
    assert calls == ["staging", "contact", "service"][: calls.index(failing_factory) + 1]
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


def test_commit_and_rollback_failures_are_generic() -> None:
    commit_session = StrictSession(commit_error=RuntimeError("commit secret"))
    outcome, session, _ = execute_with(StrictPromotionService(), session=commit_session)
    assert outcome.error_message == "Candidate promotion failed."
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (1, 1, 1)

    rollback_session = StrictSession(rollback_error=RuntimeError("rollback secret"))
    outcome, session, _ = execute_with(
        StrictPromotionService(error=ContactDiscoveryCandidatePromotionNotFoundError("private")),
        session=rollback_session,
    )
    assert outcome.error_message == "Candidate promotion failed."
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


@pytest.mark.parametrize("committed", [False, True])
def test_close_failure_is_generic_and_never_adds_rollback(committed: bool) -> None:
    session = StrictSession(close_error=RuntimeError("close secret"))
    service = (
        StrictPromotionService()
        if committed
        else StrictPromotionService(error=RuntimeError("service secret"))
    )
    outcome, session, _ = execute_with(service, session=session)
    assert outcome.error_message == "Candidate promotion failed."
    assert session.close_calls == 1
    assert session.rollback_calls == (0 if committed else 1)


@pytest.mark.parametrize("operation", ["service", "commit", "rollback", "close"])
def test_keyboard_interrupt_propagates_and_close_is_attempted(operation: str) -> None:
    session = StrictSession(
        commit_error=KeyboardInterrupt() if operation == "commit" else None,
        rollback_error=KeyboardInterrupt() if operation == "rollback" else None,
        close_error=KeyboardInterrupt() if operation == "close" else None,
    )
    service = StrictPromotionService(
        error=(
            KeyboardInterrupt()
            if operation == "service"
            else RuntimeError("trigger rollback")
            if operation == "rollback"
            else None
        )
    )
    with pytest.raises(KeyboardInterrupt):
        execute_with(service, session=session)
    assert session.close_calls == 1


def test_cli_does_not_construct_forbidden_company_or_contact_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("forbidden dependency constructed")

    monkeypatch.setattr(candidate_cli, "CompanyRepository", forbidden)
    monkeypatch.setattr(ContactRepository, "create", forbidden)
    outcome, _, _ = execute_with(StrictPromotionService())
    assert outcome.exit_code == 0

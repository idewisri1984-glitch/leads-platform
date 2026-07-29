from dataclasses import FrozenInstanceError, fields
from typing import Any, cast

import pytest
from typer.testing import CliRunner

import app.cli.lead as lead_cli
from app.cli.main import app as root_app
from app.modules.lead import (
    ContactLeadCreationConsistencyError,
    ContactLeadCreationInvalidDataError,
    ContactLeadCreationNotFoundError,
    ContactLeadCreationResult,
)

runner = CliRunner()


class StrictSession:
    def __init__(
        self,
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        close_error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.operations = operations if operations is not None else []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.operations.append("commit")
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.operations.append("rollback")
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.operations.append("close")
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class ContactRepositoryDouble:
    pass


class LeadRepositoryDouble:
    pass


class StrictService:
    def __init__(
        self,
        *,
        result: ContactLeadCreationResult | None = None,
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.result = result or creation_result()
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[tuple[int, int]] = []

    def create(self, company_id: int, contact_id: int) -> ContactLeadCreationResult:
        self.operations.append("service.create")
        self.calls.append((company_id, contact_id))
        if self.error is not None:
            raise self.error
        return self.result


def creation_result() -> ContactLeadCreationResult:
    return ContactLeadCreationResult(
        lead_id=11,
        company_id=3,
        contact_id=7,
        status="NEW",
    )


def execute_with(
    service: StrictService | None = None,
    *,
    session: StrictSession | None = None,
) -> tuple[
    lead_cli.ContactLeadCreationCommandOutcome,
    StrictSession,
    StrictService,
    list[str],
]:
    operations: list[str] = []
    selected_session = session or StrictSession(operations=operations)
    selected_session.operations = operations
    selected_service = service or StrictService(operations=operations)
    selected_service.operations = operations
    contact_repository = ContactRepositoryDouble()
    lead_repository = LeadRepositoryDouble()

    def make_contact(candidate: object) -> ContactRepositoryDouble:
        assert candidate is selected_session
        operations.append("contact_repository")
        return contact_repository

    def make_lead(candidate: object) -> LeadRepositoryDouble:
        assert candidate is selected_session
        operations.append("lead_repository")
        return lead_repository

    def make_service(
        contact: object,
        lead: object,
    ) -> StrictService:
        assert contact is contact_repository
        assert lead is lead_repository
        operations.append("service")
        return selected_service

    outcome = lead_cli.execute_create_lead_from_contact(
        company_id=3,
        contact_id=7,
        yes=True,
        session_factory=cast(lead_cli.SessionFactory, lambda: selected_session),
        contact_repository_factory=cast(lead_cli.ContactRepositoryFactory, make_contact),
        lead_repository_factory=cast(lead_cli.LeadRepositoryFactory, make_lead),
        service_factory=cast(lead_cli.ContactLeadCreationServiceFactory, make_service),
    )
    return outcome, selected_session, selected_service, operations


def test_command_group_registers_new_and_legacy_commands() -> None:
    names = {command.name for command in lead_cli.app.registered_commands}
    assert names == {"create", "create-from-contact", "list", "show", "delete"}
    result = runner.invoke(root_app, ["lead", "--help"])
    assert result.exit_code == 0
    for name in names:
        assert name in result.output


@pytest.mark.parametrize("value", [False, 0, 1, "yes", None, object()])
def test_confirmation_requires_exact_true_before_all_dependencies(value: object) -> None:
    calls: list[str] = []
    outcome = lead_cli.execute_create_lead_from_contact(
        company_id=object(),  # type: ignore[arg-type]
        contact_id=object(),  # type: ignore[arg-type]
        yes=value,  # type: ignore[arg-type]
        session_factory=cast(lead_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome == lead_cli.ContactLeadCreationCommandOutcome(
        exit_code=1,
        error_message="Lead creation requires --yes.",
    )
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_id", True),
        ("company_id", 0),
        ("company_id", -1),
        ("company_id", "3"),
        ("company_id", 3.0),
        ("company_id", None),
        ("company_id", object()),
        ("company_id", type("CompanyInt", (int,), {})(3)),
        ("contact_id", False),
        ("contact_id", 0),
        ("contact_id", -1),
        ("contact_id", "7"),
        ("contact_id", 7.0),
        ("contact_id", None),
        ("contact_id", object()),
        ("contact_id", type("ContactInt", (int,), {})(7)),
    ],
)
def test_invalid_ids_fail_before_session(field: str, value: object) -> None:
    values: dict[str, object] = {"company_id": 3, "contact_id": 7}
    values[field] = value
    calls: list[str] = []
    outcome = lead_cli.execute_create_lead_from_contact(
        company_id=values["company_id"],  # type: ignore[arg-type]
        contact_id=values["contact_id"],  # type: ignore[arg-type]
        yes=True,
        session_factory=cast(lead_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome.error_message == "Lead creation data is invalid."
    assert calls == []


def test_session_factory_ordinary_failure_is_sanitized() -> None:
    def fail() -> Any:
        raise RuntimeError("sqlite:///private.db")

    outcome = lead_cli.execute_create_lead_from_contact(
        company_id=3,
        contact_id=7,
        yes=True,
        session_factory=cast(lead_cli.SessionFactory, fail),
    )
    assert outcome.error_message == "Lead creation failed."


def test_success_uses_exact_factory_order_transaction_and_result() -> None:
    result = creation_result()
    outcome, session, service, operations = execute_with(StrictService(result=result))
    assert operations == [
        "contact_repository",
        "lead_repository",
        "service",
        "service.create",
        "commit",
        "close",
    ]
    assert service.calls == [(3, 7)]
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (1, 0, 1)
    assert outcome == lead_cli.ContactLeadCreationCommandOutcome(exit_code=0, result=result)
    assert outcome.result is result


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ContactLeadCreationInvalidDataError("private 3"), "Lead creation data is invalid."),
        (ContactLeadCreationNotFoundError("private email"), "Contact was not found."),
        (
            ContactLeadCreationConsistencyError("private database"),
            "Lead creation state is inconsistent.",
        ),
    ],
)
def test_known_errors_map_by_type_and_rollback(error: Exception, message: str) -> None:
    outcome, session, _, _ = execute_with(StrictService(error=error))
    assert outcome.error_message == message
    assert str(error) not in outcome.error_message
    assert outcome.result is None
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


def test_unexpected_service_failure_is_generic() -> None:
    error = RuntimeError("C:\\private\\database.sqlite3")
    outcome, session, _, _ = execute_with(StrictService(error=error))
    assert outcome.error_message == "Lead creation failed."
    assert str(error) not in outcome.error_message
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


@pytest.mark.parametrize("failure", ["contact", "lead", "service"])
def test_dependency_factory_failures_stop_in_order(failure: str) -> None:
    session = StrictSession()
    calls: list[str] = []

    def factory(name: str) -> object:
        calls.append(name)
        if name == failure:
            raise RuntimeError("factory secret")
        return object()

    outcome = lead_cli.execute_create_lead_from_contact(
        company_id=3,
        contact_id=7,
        yes=True,
        session_factory=cast(lead_cli.SessionFactory, lambda: session),
        contact_repository_factory=cast(
            lead_cli.ContactRepositoryFactory,
            lambda _session: factory("contact"),
        ),
        lead_repository_factory=cast(
            lead_cli.LeadRepositoryFactory,
            lambda _session: factory("lead"),
        ),
        service_factory=cast(
            lead_cli.ContactLeadCreationServiceFactory,
            lambda _contact, _lead: factory("service"),
        ),
    )
    expected = ["contact", "lead", "service"]
    assert calls == expected[: expected.index(failure) + 1]
    assert outcome.error_message == "Lead creation failed."
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


def test_commit_failure_rolls_back_and_hides_result() -> None:
    session = StrictSession(commit_error=RuntimeError("commit secret"))
    outcome, session, _, _ = execute_with(session=session)
    assert outcome == lead_cli.ContactLeadCreationCommandOutcome(
        exit_code=1,
        error_message="Lead creation failed.",
    )
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (1, 1, 1)


def test_rollback_failure_is_generic_without_retry() -> None:
    session = StrictSession(rollback_error=RuntimeError("rollback secret"))
    outcome, session, _, _ = execute_with(
        StrictService(error=ContactLeadCreationNotFoundError("private")),
        session=session,
    )
    assert outcome.error_message == "Lead creation failed."
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


@pytest.mark.parametrize("committed", [False, True])
def test_close_failure_is_generic_and_never_adds_rollback(committed: bool) -> None:
    session = StrictSession(close_error=RuntimeError("close secret"))
    service = StrictService() if committed else StrictService(error=RuntimeError("service secret"))
    outcome, session, _, _ = execute_with(service, session=session)
    assert outcome.error_message == "Lead creation failed."
    assert session.close_calls == 1
    assert session.rollback_calls == (0 if committed else 1)


@pytest.mark.parametrize(
    "operation",
    ["session", "contact", "lead", "factory", "service", "commit", "rollback", "close"],
)
def test_keyboard_interrupt_propagates_and_closes_when_session_exists(operation: str) -> None:
    session = StrictSession(
        commit_error=KeyboardInterrupt() if operation == "commit" else None,
        rollback_error=KeyboardInterrupt() if operation == "rollback" else None,
        close_error=KeyboardInterrupt() if operation == "close" else None,
    )

    def fail_at(name: str, value: object) -> object:
        if operation == name:
            raise KeyboardInterrupt()
        return value

    service = StrictService(
        error=(
            KeyboardInterrupt()
            if operation == "service"
            else RuntimeError("trigger rollback")
            if operation == "rollback"
            else None
        )
    )
    with pytest.raises(KeyboardInterrupt):
        lead_cli.execute_create_lead_from_contact(
            company_id=3,
            contact_id=7,
            yes=True,
            session_factory=cast(
                lead_cli.SessionFactory,
                lambda: fail_at("session", session),
            ),
            contact_repository_factory=cast(
                lead_cli.ContactRepositoryFactory,
                lambda _session: fail_at("contact", ContactRepositoryDouble()),
            ),
            lead_repository_factory=cast(
                lead_cli.LeadRepositoryFactory,
                lambda _session: fail_at("lead", LeadRepositoryDouble()),
            ),
            service_factory=cast(
                lead_cli.ContactLeadCreationServiceFactory,
                lambda _contact, _lead: fail_at("factory", service),
            ),
        )
    assert session.close_calls == (0 if operation == "session" else 1)
    assert session.rollback_calls <= 1


def test_system_exit_is_not_swallowed() -> None:
    with pytest.raises(SystemExit) as captured:
        execute_with(StrictService(error=SystemExit(29)))
    assert captured.value.code == 29


def test_actual_root_cli_prints_exact_safe_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lead_cli,
        "execute_create_lead_from_contact",
        lambda **_kwargs: lead_cli.ContactLeadCreationCommandOutcome(
            exit_code=0,
            result=creation_result(),
        ),
    )
    result = runner.invoke(
        root_app,
        [
            "lead",
            "create-from-contact",
            "--company-id",
            "3",
            "--contact-id",
            "7",
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "Lead ID: 11",
        "Company ID: 3",
        "Contact ID: 7",
        "Status: NEW",
    ]
    lowered = result.output.lower()
    for forbidden in (
        "name",
        "email",
        "phone",
        "title",
        "source",
        "notes",
        "sql",
        "database",
        "traceback",
        "created",
        "changed",
    ):
        assert forbidden not in lowered


def test_actual_root_cli_prints_fixed_controlled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lead_cli,
        "execute_create_lead_from_contact",
        lambda **_kwargs: lead_cli.ContactLeadCreationCommandOutcome(
            exit_code=1,
            error_message="Lead creation failed.",
        ),
    )
    result = runner.invoke(
        root_app,
        [
            "lead",
            "create-from-contact",
            "--company-id",
            "3",
            "--contact-id",
            "7",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert result.output.strip() == "Lead creation failed."
    assert "Traceback" not in result.output


def test_legacy_create_is_not_redirected_to_new_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(**_kwargs: object) -> None:
        pytest.fail("legacy create invoked the new executor")

    monkeypatch.setattr(lead_cli, "execute_create_lead_from_contact", forbidden)
    result = runner.invoke(root_app, ["lead", "create", "--help"])
    assert result.exit_code == 0
    assert "create-from-contact" not in result.output


def test_outcome_is_frozen_and_has_only_safe_fields() -> None:
    outcome = lead_cli.ContactLeadCreationCommandOutcome(exit_code=1, error_message="safe")
    assert [field.name for field in fields(outcome)] == [
        "exit_code",
        "result",
        "error_message",
    ]
    with pytest.raises(FrozenInstanceError):
        outcome.exit_code = 0
    assert not any(
        hasattr(outcome, name)
        for name in ("session", "repository", "service", "exception", "lead", "contact")
    )


def test_new_executor_has_no_forbidden_domain_dependencies() -> None:
    for name in (
        "CompanyRepository",
        "CompanyService",
        "ContactService",
        "TaskRepository",
        "TaskService",
    ):
        assert not hasattr(lead_cli, name)

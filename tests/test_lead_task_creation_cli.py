import inspect
from dataclasses import FrozenInstanceError, fields
from typing import Any, cast

import pytest
from typer.testing import CliRunner

import app.cli.task as task_cli
from app.cli.main import app as root_app
from app.modules.task import (
    LeadTaskCreationConsistencyError,
    LeadTaskCreationInvalidDataError,
    LeadTaskCreationNotFoundError,
    LeadTaskCreationResult,
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


class LeadRepositoryDouble:
    def get(self, _lead_id: int) -> None:
        pytest.fail("generic Lead lookup was used")

    def get_by_company(self, _company_id: int) -> None:
        pytest.fail("Lead list lookup was used")


class TaskRepositoryDouble:
    def create(self, _data: object) -> None:
        pytest.fail("generic Task creation was used")

    def get(self, _task_id: int) -> None:
        pytest.fail("Task lookup was used")

    def get_by_lead(self, _lead_id: int) -> None:
        pytest.fail("duplicate lookup was used")


class StrictService:
    def __init__(
        self,
        *,
        result: LeadTaskCreationResult | None = None,
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.result = result or creation_result()
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[tuple[int, int, str, str | None]] = []

    def create(
        self,
        company_id: int,
        lead_id: int,
        title: str,
        description: str | None,
    ) -> LeadTaskCreationResult:
        self.operations.append("service.create")
        self.calls.append((company_id, lead_id, title, description))
        if self.error is not None:
            raise self.error
        return self.result


def creation_result(task_id: int = 13) -> LeadTaskCreationResult:
    return LeadTaskCreationResult(
        task_id=task_id,
        company_id=3,
        lead_id=7,
        status="TODO",
    )


def execute_with(
    service: StrictService | None = None,
    *,
    session: StrictSession | None = None,
    title: str = "  Follow up  ",
    description: str | None = "  Call tomorrow  ",
) -> tuple[
    task_cli.LeadTaskCreationCommandOutcome,
    StrictSession,
    StrictService,
    list[str],
]:
    operations: list[str] = []
    selected_session = session or StrictSession(operations=operations)
    selected_session.operations = operations
    selected_service = service or StrictService(operations=operations)
    selected_service.operations = operations
    lead_repository = LeadRepositoryDouble()
    task_repository = TaskRepositoryDouble()

    def make_lead(candidate: object) -> LeadRepositoryDouble:
        assert candidate is selected_session
        operations.append("lead_repository")
        return lead_repository

    def make_task(candidate: object) -> TaskRepositoryDouble:
        assert candidate is selected_session
        operations.append("task_repository")
        return task_repository

    def make_service(
        lead: object,
        task: object,
    ) -> StrictService:
        assert lead is lead_repository
        assert task is task_repository
        operations.append("service")
        return selected_service

    outcome = task_cli.execute_create_task_for_lead(
        company_id=3,
        lead_id=7,
        title=title,
        description=description,
        yes=True,
        session_factory=cast(task_cli.SessionFactory, lambda: selected_session),
        lead_repository_factory=cast(task_cli.LeadRepositoryFactory, make_lead),
        task_repository_factory=cast(task_cli.TaskRepositoryFactory, make_task),
        service_factory=cast(task_cli.LeadTaskCreationServiceFactory, make_service),
    )
    return outcome, selected_session, selected_service, operations


def test_command_group_and_help_contract_are_exact() -> None:
    names = {command.name for command in task_cli.app.registered_commands}
    assert names == {"create", "create-for-lead", "list", "show", "delete"}
    result = runner.invoke(root_app, ["task", "create-for-lead", "--help"])
    assert result.exit_code == 0
    for option in ("--company-id", "--lead-id", "--title", "--description", "--yes"):
        assert option in result.output
    for forbidden in (
        "--status",
        "--due-at",
        "--dry-run",
        "--force",
        "--owner",
        "--priority",
        "--source",
    ):
        assert forbidden not in result.output


def test_executor_signature_and_outcome_are_exact_and_frozen() -> None:
    signature = inspect.signature(task_cli.execute_create_task_for_lead)
    assert list(signature.parameters) == [
        "company_id",
        "lead_id",
        "title",
        "yes",
        "description",
        "session_factory",
        "lead_repository_factory",
        "task_repository_factory",
        "service_factory",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.parameters["description"].default is None
    outcome = task_cli.LeadTaskCreationCommandOutcome(exit_code=1, error_message="safe")
    assert [field.name for field in fields(outcome)] == [
        "exit_code",
        "result",
        "error_message",
    ]
    with pytest.raises(FrozenInstanceError):
        outcome.exit_code = 0


@pytest.mark.parametrize("value", [False, 0, 1, "yes", None, object()])
def test_confirmation_requires_exact_true_before_all_other_work(value: object) -> None:
    calls: list[str] = []
    outcome = task_cli.execute_create_task_for_lead(
        company_id=object(),  # type: ignore[arg-type]
        lead_id=object(),  # type: ignore[arg-type]
        title=object(),  # type: ignore[arg-type]
        description=object(),  # type: ignore[arg-type]
        yes=value,  # type: ignore[arg-type]
        session_factory=cast(task_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome == task_cli.LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message="Task creation requires --yes.",
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
        ("lead_id", False),
        ("lead_id", 0),
        ("lead_id", -1),
        ("lead_id", "7"),
        ("lead_id", 7.0),
        ("lead_id", None),
        ("lead_id", object()),
        ("lead_id", type("LeadInt", (int,), {})(7)),
        ("title", None),
        ("title", True),
        ("title", 1),
        ("title", b"title"),
        ("title", object()),
        ("title", type("Title", (str,), {})("title")),
        ("title", ""),
        ("title", " \t "),
        ("title", "x" * 256),
        ("description", True),
        ("description", 1),
        ("description", b"description"),
        ("description", object()),
        ("description", type("Description", (str,), {})("description")),
    ],
)
def test_invalid_inputs_fail_before_session(field: str, value: object) -> None:
    values: dict[str, object] = {
        "company_id": 3,
        "lead_id": 7,
        "title": "Follow up",
        "description": None,
    }
    values[field] = value
    calls: list[str] = []
    outcome = task_cli.execute_create_task_for_lead(
        company_id=values["company_id"],  # type: ignore[arg-type]
        lead_id=values["lead_id"],  # type: ignore[arg-type]
        title=values["title"],  # type: ignore[arg-type]
        description=values["description"],  # type: ignore[arg-type]
        yes=True,
        session_factory=cast(task_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome == task_cli.LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message="Task creation data is invalid.",
    )
    assert calls == []


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("x" * 255, None),
        ("  Follow up  ", ""),
        ("Follow up", "  Call tomorrow  "),
    ],
)
def test_valid_boundaries_preserve_exact_text(
    title: str,
    description: str | None,
) -> None:
    outcome, _, service, _ = execute_with(title=title, description=description)
    assert outcome.exit_code == 0
    assert service.calls == [(3, 7, title, description)]


def test_session_factory_ordinary_failure_is_sanitized() -> None:
    def fail() -> Any:
        raise RuntimeError("sqlite:///private.db")

    outcome = task_cli.execute_create_task_for_lead(
        company_id=3,
        lead_id=7,
        title="Follow up",
        yes=True,
        session_factory=cast(task_cli.SessionFactory, fail),
    )
    assert outcome == task_cli.LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message="Task creation failed.",
    )


def test_success_uses_exact_order_identity_transaction_and_result() -> None:
    result = creation_result()
    outcome, session, service, operations = execute_with(StrictService(result=result))
    assert operations == [
        "lead_repository",
        "task_repository",
        "service",
        "service.create",
        "commit",
        "close",
    ]
    assert service.calls == [(3, 7, "  Follow up  ", "  Call tomorrow  ")]
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (1, 0, 1)
    assert outcome == task_cli.LeadTaskCreationCommandOutcome(exit_code=0, result=result)
    assert outcome.result is result


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (LeadTaskCreationInvalidDataError("private title"), "Task creation data is invalid."),
        (LeadTaskCreationNotFoundError("private lead"), "Lead was not found."),
        (
            LeadTaskCreationConsistencyError("private database"),
            "Task creation state is inconsistent.",
        ),
    ],
)
def test_controlled_errors_map_by_type_and_cleanup(error: Exception, message: str) -> None:
    outcome, session, _, _ = execute_with(StrictService(error=error))
    assert outcome == task_cli.LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message=message,
    )
    assert str(error) not in outcome.error_message
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


def test_unexpected_service_failure_is_generic_and_sanitized() -> None:
    error = RuntimeError("C:\\private\\database.sqlite3")
    outcome, session, _, _ = execute_with(StrictService(error=error))
    assert outcome.error_message == "Task creation failed."
    assert str(error) not in outcome.error_message
    assert outcome.result is None
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


@pytest.mark.parametrize("failure", ["lead", "task", "service"])
def test_dependency_factory_ordinary_failures_cleanup_once(failure: str) -> None:
    session = StrictSession()
    calls: list[str] = []

    def factory(name: str) -> object:
        calls.append(name)
        if name == failure:
            raise RuntimeError("factory secret")
        return object()

    outcome = task_cli.execute_create_task_for_lead(
        company_id=3,
        lead_id=7,
        title="Follow up",
        yes=True,
        session_factory=cast(task_cli.SessionFactory, lambda: session),
        lead_repository_factory=cast(
            task_cli.LeadRepositoryFactory,
            lambda _session: factory("lead"),
        ),
        task_repository_factory=cast(
            task_cli.TaskRepositoryFactory,
            lambda _session: factory("task"),
        ),
        service_factory=cast(
            task_cli.LeadTaskCreationServiceFactory,
            lambda _lead, _task: factory("service"),
        ),
    )
    expected = ["lead", "task", "service"]
    assert calls == expected[: expected.index(failure) + 1]
    assert outcome.error_message == "Task creation failed."
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


def test_commit_ordinary_failure_rolls_back_and_hides_result() -> None:
    session = StrictSession(commit_error=RuntimeError("commit secret"))
    outcome, session, _, _ = execute_with(session=session)
    assert outcome == task_cli.LeadTaskCreationCommandOutcome(
        exit_code=1,
        error_message="Task creation failed.",
    )
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (1, 1, 1)


def test_rollback_ordinary_failure_is_not_retried() -> None:
    session = StrictSession(rollback_error=RuntimeError("rollback secret"))
    outcome, session, _, _ = execute_with(
        StrictService(error=LeadTaskCreationNotFoundError("private")),
        session=session,
    )
    assert outcome.error_message == "Task creation failed."
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


@pytest.mark.parametrize("committed", [False, True])
def test_close_ordinary_failure_is_generic_without_retry(committed: bool) -> None:
    session = StrictSession(close_error=RuntimeError("close secret"))
    service = (
        StrictService()
        if committed
        else StrictService(error=LeadTaskCreationNotFoundError("private"))
    )
    outcome, session, _, _ = execute_with(service, session=session)
    assert outcome.error_message == "Task creation failed."
    assert session.close_calls == 1
    assert session.rollback_calls == (0 if committed else 1)
    assert session.commit_calls == (1 if committed else 0)


def test_session_factory_baseexception_propagates_without_cleanup() -> None:
    primary = KeyboardInterrupt("session interrupted")

    def fail() -> Any:
        raise primary

    with pytest.raises(KeyboardInterrupt) as captured:
        task_cli.execute_create_task_for_lead(
            company_id=3,
            lead_id=7,
            title="Follow up",
            yes=True,
            session_factory=cast(task_cli.SessionFactory, fail),
        )
    assert captured.value is primary


@pytest.mark.parametrize("failure", ["lead", "task", "service_factory", "service", "commit"])
def test_primary_baseexception_survives_secondary_close_failure(failure: str) -> None:
    primary = KeyboardInterrupt(f"{failure} interrupted")
    secondary = SystemExit("close interrupted")
    session = StrictSession(
        commit_error=primary if failure == "commit" else None,
        close_error=secondary,
    )
    service = StrictService(error=primary if failure == "service" else None)

    def make(name: str) -> object:
        if failure == name:
            raise primary
        return object()

    with pytest.raises(KeyboardInterrupt) as captured:
        task_cli.execute_create_task_for_lead(
            company_id=3,
            lead_id=7,
            title="Follow up",
            yes=True,
            session_factory=cast(task_cli.SessionFactory, lambda: session),
            lead_repository_factory=cast(
                task_cli.LeadRepositoryFactory,
                lambda _session: make("lead"),
            ),
            task_repository_factory=cast(
                task_cli.TaskRepositoryFactory,
                lambda _session: make("task"),
            ),
            service_factory=cast(
                task_cli.LeadTaskCreationServiceFactory,
                lambda _lead, _task: (
                    make("service_factory") if failure == "service_factory" else service
                ),
            ),
        )
    assert captured.value is primary
    assert captured.value.args == (f"{failure} interrupted",)
    assert captured.value.__cause__ is None
    assert session.close_calls == 1
    assert session.rollback_calls == 0


def test_rollback_primary_baseexception_survives_secondary_close_failure() -> None:
    trigger = LeadTaskCreationNotFoundError("private")
    primary = KeyboardInterrupt("rollback interrupted")
    session = StrictSession(
        rollback_error=primary,
        close_error=SystemExit("close interrupted"),
    )
    with pytest.raises(KeyboardInterrupt) as captured:
        execute_with(StrictService(error=trigger), session=session)
    assert captured.value is primary
    assert captured.value.args == ("rollback interrupted",)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is trigger
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (0, 1, 1)


def test_first_close_baseexception_propagates_after_commit() -> None:
    primary = KeyboardInterrupt("close interrupted")
    session = StrictSession(close_error=primary)
    with pytest.raises(KeyboardInterrupt) as captured:
        execute_with(session=session)
    assert captured.value is primary
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (1, 0, 1)


def test_executor_emits_no_output(capsys: pytest.CaptureFixture[str]) -> None:
    outcome, _, _, _ = execute_with()
    assert outcome.exit_code == 0
    assert capsys.readouterr() == ("", "")


def test_root_command_prints_exact_safe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_cli,
        "execute_create_task_for_lead",
        lambda **_kwargs: task_cli.LeadTaskCreationCommandOutcome(
            exit_code=0,
            result=creation_result(),
        ),
    )
    result = runner.invoke(
        root_app,
        [
            "task",
            "create-for-lead",
            "--company-id",
            "3",
            "--lead-id",
            "7",
            "--title",
            "private title",
            "--description",
            "private description",
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "Task ID: 13",
        "Company ID: 3",
        "Lead ID: 7",
        "Status: TODO",
    ]
    for forbidden in ("private", "title", "description", "due", "database", "traceback"):
        assert forbidden not in result.output.lower()


def test_root_command_prints_one_fixed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_cli,
        "execute_create_task_for_lead",
        lambda **_kwargs: task_cli.LeadTaskCreationCommandOutcome(
            exit_code=1,
            error_message="Task creation failed.",
        ),
    )
    result = runner.invoke(
        root_app,
        [
            "task",
            "create-for-lead",
            "--company-id",
            "3",
            "--lead-id",
            "7",
            "--title",
            "Follow up",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert result.output.strip() == "Task creation failed."
    assert "Traceback" not in result.output


def test_legacy_create_is_independent_of_new_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(**_kwargs: object) -> None:
        pytest.fail("legacy create invoked the new executor")

    monkeypatch.setattr(task_cli, "execute_create_task_for_lead", forbidden)
    result = runner.invoke(root_app, ["task", "create", "--help"])
    assert result.exit_code == 0
    assert "--status" in result.output
    assert "--due-at" in result.output


def test_repeated_execution_is_non_idempotent() -> None:
    first, first_session, first_service, _ = execute_with(StrictService(result=creation_result(13)))
    second, second_session, second_service, _ = execute_with(
        StrictService(result=creation_result(14))
    )
    assert first.result is not None and second.result is not None
    assert first.result.task_id != second.result.task_id
    assert len(first_service.calls) == len(second_service.calls) == 1
    assert first_session.commit_calls == second_session.commit_calls == 1

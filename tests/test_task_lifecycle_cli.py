import inspect
from dataclasses import FrozenInstanceError, fields
from enum import Enum
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

import app.cli.task as task_cli
from app.cli.main import app as root_app
from app.modules.task import (
    TaskLifecycleConsistencyError,
    TaskLifecycleInvalidDataError,
    TaskLifecycleNotFoundError,
    TaskLifecycleResult,
    TaskLifecycleStatus,
    TaskLifecycleTransitionError,
)

runner = CliRunner()


class IntSubclass(int):
    pass


class StringSubclass(str):
    pass


class OtherStatus(Enum):
    IN_PROGRESS = "IN_PROGRESS"


class PrimaryFailure(BaseException):
    pass


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

    def add(self, _value: object) -> None:
        pytest.fail("executor called Session.add")

    def flush(self) -> None:
        pytest.fail("executor called Session.flush")

    def execute(self, _statement: object) -> None:
        pytest.fail("executor executed SQL")

    def begin(self) -> None:
        pytest.fail("executor began a transaction")

    def begin_nested(self) -> None:
        pytest.fail("executor began a nested transaction")


class StrictRepository:
    def get(self, _task_id: int) -> None:
        pytest.fail("executor used generic repository get")

    def create(self, _data: object) -> None:
        pytest.fail("executor used generic repository create")

    def update(self, _task: object) -> None:
        pytest.fail("executor used generic repository update")

    def delete(self, _task: object) -> None:
        pytest.fail("executor used generic repository delete")

    def set_status_for_company(self, *_args: object) -> None:
        pytest.fail("executor called lifecycle repository directly")


class StrictService:
    def __init__(
        self,
        *,
        result: TaskLifecycleResult | None = None,
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.result = result or lifecycle_result()
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[tuple[int, int, TaskLifecycleStatus]] = []

    def transition(
        self,
        company_id: int,
        task_id: int,
        target_status: TaskLifecycleStatus,
    ) -> TaskLifecycleResult:
        self.operations.append("service.transition")
        self.calls.append((company_id, task_id, target_status))
        if self.error is not None:
            raise self.error
        return self.result


def lifecycle_result(
    *,
    previous: TaskLifecycleStatus = TaskLifecycleStatus.TODO,
    current: TaskLifecycleStatus = TaskLifecycleStatus.IN_PROGRESS,
    changed: bool = True,
) -> TaskLifecycleResult:
    return TaskLifecycleResult(
        task_id=12,
        company_id=3,
        previous_status=previous,
        current_status=current,
        changed=changed,
    )


def execute_with(
    service: StrictService | None = None,
    *,
    session: StrictSession | None = None,
    target: TaskLifecycleStatus = TaskLifecycleStatus.IN_PROGRESS,
) -> tuple[
    task_cli.TaskLifecycleCommandOutcome,
    StrictSession,
    StrictRepository,
    StrictService,
    list[str],
]:
    operations: list[str] = []
    selected_session = session or StrictSession()
    selected_session.operations = operations
    repository = StrictRepository()
    selected_service = service or StrictService()
    selected_service.operations = operations

    def make_repository(candidate: object) -> StrictRepository:
        assert candidate is selected_session
        operations.append("repository")
        return repository

    def make_service(candidate: object) -> StrictService:
        assert candidate is repository
        operations.append("service")
        return selected_service

    outcome = task_cli.execute_task_lifecycle_transition(
        company_id=3,
        task_id=12,
        target_status=target,
        yes=True,
        session_factory=cast(task_cli.SessionFactory, lambda: selected_session),
        task_repository_factory=cast(
            task_cli.TaskRepositoryFactory,
            make_repository,
        ),
        service_factory=cast(task_cli.TaskLifecycleServiceFactory, make_service),
    )
    return outcome, selected_session, repository, selected_service, operations


def test_task_command_set_is_exact() -> None:
    assert {command.name for command in task_cli.app.registered_commands} == {
        "create",
        "create-for-lead",
        "list",
        "show",
        "delete",
        "start",
        "complete",
        "cancel",
    }


@pytest.mark.parametrize("command", ["start", "complete", "cancel"])
def test_lifecycle_command_help_is_exact(command: str) -> None:
    result = runner.invoke(root_app, ["task", command, "--help"])
    assert result.exit_code == 0
    for option in ("--company-id", "--task-id", "--yes", "--help"):
        assert option in result.output
    for forbidden in (
        "--target-status",
        "--status",
        "--from-status",
        "--previous-status",
        "--force",
        "--dry-run",
        "--interactive",
        "--reason",
        "--note",
        "--lead-id",
        "--due-at",
        "--owner",
        "--priority",
        "--notify",
        "--schedule",
    ):
        assert forbidden not in result.output


def test_executor_signature_and_outcome_contract_are_exact() -> None:
    signature = inspect.signature(task_cli.execute_task_lifecycle_transition)
    assert list(signature.parameters) == [
        "company_id",
        "task_id",
        "target_status",
        "yes",
        "session_factory",
        "task_repository_factory",
        "service_factory",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    outcome = task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="safe",
    )
    assert [field.name for field in fields(outcome)] == [
        "exit_code",
        "result",
        "error_message",
    ]
    assert outcome.result is None
    with pytest.raises(FrozenInstanceError):
        outcome.exit_code = 0


@pytest.mark.parametrize("value", [False, 0, 1, "yes", None, object()])
def test_confirmation_requires_exact_true_before_validation(value: object) -> None:
    calls: list[str] = []
    outcome = task_cli.execute_task_lifecycle_transition(
        company_id=object(),  # type: ignore[arg-type]
        task_id=object(),  # type: ignore[arg-type]
        target_status=object(),  # type: ignore[arg-type]
        yes=value,  # type: ignore[arg-type]
        session_factory=cast(task_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition requires --yes.",
    )
    assert calls == []


@pytest.mark.parametrize(
    "value",
    [True, 0, -1, "3", 3.0, None, object(), IntSubclass(3)],
)
@pytest.mark.parametrize("field", ["company_id", "task_id"])
def test_identifiers_require_exact_positive_int(field: str, value: object) -> None:
    calls: list[str] = []
    arguments: dict[str, Any] = {
        "company_id": 3,
        "task_id": 12,
        "target_status": TaskLifecycleStatus.IN_PROGRESS,
        "yes": True,
        "session_factory": cast(
            task_cli.SessionFactory,
            lambda: calls.append("session"),
        ),
    }
    arguments[field] = value
    outcome = task_cli.execute_task_lifecycle_transition(**arguments)
    assert outcome.error_message == "Task lifecycle data is invalid."
    assert calls == []


@pytest.mark.parametrize(
    "value",
    [
        "IN_PROGRESS",
        "invalid",
        OtherStatus.IN_PROGRESS,
        True,
        1,
        None,
        object(),
        StringSubclass("IN_PROGRESS"),
    ],
)
def test_target_status_requires_exact_enum_member(value: object) -> None:
    calls: list[str] = []
    outcome = task_cli.execute_task_lifecycle_transition(
        company_id=3,
        task_id=12,
        target_status=value,  # type: ignore[arg-type]
        yes=True,
        session_factory=cast(task_cli.SessionFactory, lambda: calls.append("session")),
    )
    assert outcome.error_message == "Task lifecycle data is invalid."
    assert calls == []


def test_session_factory_ordinary_failure_is_sanitized() -> None:
    def fail() -> None:
        raise RuntimeError("private database path")

    outcome = task_cli.execute_task_lifecycle_transition(
        company_id=3,
        task_id=12,
        target_status=TaskLifecycleStatus.IN_PROGRESS,
        yes=True,
        session_factory=cast(task_cli.SessionFactory, fail),
    )
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )


def test_session_factory_baseexception_propagates_by_identity() -> None:
    primary = PrimaryFailure("private")

    def fail() -> None:
        raise primary

    with pytest.raises(PrimaryFailure) as raised:
        task_cli.execute_task_lifecycle_transition(
            company_id=3,
            task_id=12,
            target_status=TaskLifecycleStatus.IN_PROGRESS,
            yes=True,
            session_factory=cast(task_cli.SessionFactory, fail),
        )
    assert raised.value is primary


def test_success_wiring_order_call_identity_and_result_identity() -> None:
    result = lifecycle_result()
    service = StrictService(result=result)
    outcome, session, _, selected_service, operations = execute_with(service)
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=0,
        result=result,
    )
    assert outcome.result is result
    assert selected_service.calls == [
        (3, 12, TaskLifecycleStatus.IN_PROGRESS),
    ]
    assert operations == [
        "repository",
        "service",
        "service.transition",
        "commit",
        "close",
    ]
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        1,
        0,
        1,
    )


def test_idempotent_success_still_commits() -> None:
    result = lifecycle_result(
        previous=TaskLifecycleStatus.IN_PROGRESS,
        current=TaskLifecycleStatus.IN_PROGRESS,
        changed=False,
    )
    outcome, session, _, _, _ = execute_with(StrictService(result=result))
    assert outcome.result is result
    assert session.commit_calls == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            TaskLifecycleInvalidDataError("private invalid"),
            "Task lifecycle data is invalid.",
        ),
        (TaskLifecycleNotFoundError("private missing"), "Task was not found."),
        (
            TaskLifecycleTransitionError("private status"),
            "Task status transition is not allowed.",
        ),
        (
            TaskLifecycleConsistencyError("private SQL"),
            "Task lifecycle state is inconsistent.",
        ),
    ],
)
def test_controlled_errors_map_to_fixed_messages(
    error: BaseException,
    message: str,
) -> None:
    outcome, session, _, _, _ = execute_with(StrictService(error=error))
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message=message,
    )
    assert "private" not in repr(outcome)
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        0,
        1,
        1,
    )


@pytest.mark.parametrize("stage", ["repository", "service", "transition"])
def test_ordinary_dependency_and_service_failures_are_sanitized(stage: str) -> None:
    session = StrictSession()
    repository = StrictRepository()

    def make_repository(_session: object) -> StrictRepository:
        if stage == "repository":
            raise RuntimeError("private repository SQL")
        return repository

    def make_service(_repository: object) -> StrictService:
        if stage == "service":
            raise ValueError("private service")
        return StrictService(
            error=RuntimeError("private transition") if stage == "transition" else None
        )

    outcome = task_cli.execute_task_lifecycle_transition(
        company_id=3,
        task_id=12,
        target_status=TaskLifecycleStatus.IN_PROGRESS,
        yes=True,
        session_factory=cast(task_cli.SessionFactory, lambda: session),
        task_repository_factory=cast(
            task_cli.TaskRepositoryFactory,
            make_repository,
        ),
        service_factory=cast(task_cli.TaskLifecycleServiceFactory, make_service),
    )
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        0,
        1,
        1,
    )


def test_commit_failure_rolls_back_and_closes() -> None:
    session = StrictSession(commit_error=RuntimeError("private commit"))
    outcome, session, _, _, operations = execute_with(session=session)
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )
    assert operations[-3:] == ["commit", "rollback", "close"]
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        1,
        1,
        1,
    )


def test_rollback_failure_is_not_retried() -> None:
    session = StrictSession(rollback_error=RuntimeError("private rollback"))
    outcome, session, _, _, _ = execute_with(
        StrictService(error=TaskLifecycleNotFoundError("private")),
        session=session,
    )
    assert outcome.error_message == "Task lifecycle transition failed."
    assert (session.rollback_calls, session.close_calls) == (1, 1)


def test_close_ordinary_failure_before_commit_returns_generic() -> None:
    session = StrictSession(close_error=RuntimeError("private close"))
    outcome, session, _, _, _ = execute_with(
        StrictService(error=TaskLifecycleNotFoundError("private")),
        session=session,
    )
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        0,
        1,
        1,
    )


def test_close_ordinary_failure_after_commit_returns_generic_without_result() -> None:
    session = StrictSession(close_error=RuntimeError("private close"))
    outcome, session, _, _, _ = execute_with(session=session)
    assert outcome == task_cli.TaskLifecycleCommandOutcome(
        exit_code=1,
        error_message="Task lifecycle transition failed.",
    )
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        1,
        0,
        1,
    )


@pytest.mark.parametrize("stage", ["repository", "service", "transition", "commit"])
def test_primary_baseexception_propagates_and_close_failure_is_suppressed(
    stage: str,
) -> None:
    primary = PrimaryFailure(stage)
    session = StrictSession(close_error=SystemExit("cleanup"))
    repository = StrictRepository()

    def make_repository(_session: object) -> StrictRepository:
        if stage == "repository":
            raise primary
        return repository

    def make_service(_repository: object) -> StrictService:
        if stage == "service":
            raise primary
        return StrictService(error=primary if stage == "transition" else None)

    if stage == "commit":
        session.commit_error = primary
    with pytest.raises(PrimaryFailure) as raised:
        task_cli.execute_task_lifecycle_transition(
            company_id=3,
            task_id=12,
            target_status=TaskLifecycleStatus.IN_PROGRESS,
            yes=True,
            session_factory=cast(task_cli.SessionFactory, lambda: session),
            task_repository_factory=cast(
                task_cli.TaskRepositoryFactory,
                make_repository,
            ),
            service_factory=cast(task_cli.TaskLifecycleServiceFactory, make_service),
        )
    assert raised.value is primary
    assert session.close_calls == 1


def test_rollback_baseexception_propagates_without_retry() -> None:
    primary = KeyboardInterrupt()
    session = StrictSession(rollback_error=primary)
    with pytest.raises(KeyboardInterrupt) as raised:
        execute_with(
            StrictService(error=TaskLifecycleNotFoundError("private")),
            session=session,
        )
    assert raised.value is primary
    assert (session.rollback_calls, session.close_calls) == (1, 1)


@pytest.mark.parametrize("error", [SystemExit(), GeneratorExit(), PrimaryFailure()])
def test_normal_close_baseexception_propagates_by_identity(error: BaseException) -> None:
    session = StrictSession(close_error=error)
    with pytest.raises(type(error)) as raised:
        execute_with(session=session)
    assert raised.value is error
    assert (session.commit_calls, session.rollback_calls, session.close_calls) == (
        1,
        0,
        1,
    )


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("start", TaskLifecycleStatus.IN_PROGRESS),
        ("complete", TaskLifecycleStatus.DONE),
        ("cancel", TaskLifecycleStatus.CANCELLED),
    ],
)
def test_wrappers_use_fixed_targets_and_print_exact_success(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    target: TaskLifecycleStatus,
) -> None:
    calls: list[dict[str, object]] = []
    result_value = lifecycle_result(
        previous=target,
        current=target,
        changed=False,
    )

    def execute(**kwargs: object) -> task_cli.TaskLifecycleCommandOutcome:
        calls.append(kwargs)
        return task_cli.TaskLifecycleCommandOutcome(
            exit_code=0,
            result=result_value,
        )

    monkeypatch.setattr(task_cli, "execute_task_lifecycle_transition", execute)
    result = runner.invoke(
        root_app,
        [
            "task",
            command,
            "--company-id",
            "3",
            "--task-id",
            "12",
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert calls == [
        {
            "company_id": 3,
            "task_id": 12,
            "target_status": target,
            "yes": True,
        }
    ]
    assert result.output.splitlines() == [
        "Task ID: 12",
        "Company ID: 3",
        f"Previous Status: {target.value}",
        f"Current Status: {target.value}",
        "Changed: false",
    ]


def test_changed_success_and_failure_output_are_exact_and_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed = lifecycle_result()
    monkeypatch.setattr(
        task_cli,
        "execute_task_lifecycle_transition",
        lambda **_kwargs: task_cli.TaskLifecycleCommandOutcome(
            exit_code=0,
            result=changed,
        ),
    )
    success = runner.invoke(
        root_app,
        ["task", "start", "--company-id", "3", "--task-id", "12", "--yes"],
    )
    assert success.output.splitlines() == [
        "Task ID: 12",
        "Company ID: 3",
        "Previous Status: TODO",
        "Current Status: IN_PROGRESS",
        "Changed: true",
    ]
    monkeypatch.setattr(
        task_cli,
        "execute_task_lifecycle_transition",
        lambda **_kwargs: task_cli.TaskLifecycleCommandOutcome(
            exit_code=1,
            error_message="Task lifecycle transition failed.",
        ),
    )
    failure = runner.invoke(
        root_app,
        ["task", "start", "--company-id", "3", "--task-id", "12", "--yes"],
    )
    assert failure.exit_code == 1
    assert failure.output.strip() == "Task lifecycle transition failed."
    for marker in (
        "private",
        "title",
        "description",
        "lead id",
        "due_at",
        "select ",
        "traceback",
        "\\",
    ):
        assert marker not in failure.output.lower()


def test_direct_executor_is_silent(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    outcome, _, _, _, _ = execute_with()
    assert outcome.exit_code == 0
    assert capsys.readouterr() == ("", "")
    assert caplog.records == []


def test_existing_commands_do_not_construct_lifecycle_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []
    task = SimpleNamespace(
        id=12,
        lead_id=7,
        title="Legacy task",
        description="Legacy description",
        status="WAITING_CUSTOMER",
        due_at=None,
    )

    class ForbiddenTaskLifecycleService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("Legacy Task command constructed TaskLifecycleService.")

    class LegacySession:
        def __enter__(self) -> "LegacySession":
            operations.append("session.enter")
            return self

        def __exit__(self, *_args: object) -> None:
            operations.append("session.exit")

        def commit(self) -> None:
            operations.append("session.commit")

        def rollback(self) -> None:
            pytest.fail("successful legacy command rolled back")

        def close(self) -> None:
            operations.append("session.close")

    class LegacyTaskRepository:
        def __init__(self, session: object) -> None:
            assert isinstance(session, LegacySession)
            operations.append("task.repository")

        def get(self, task_id: int) -> object:
            assert task_id == 12
            operations.append("task.repository.get")
            return task

        def set_status_for_company(self, *_args: object) -> None:
            pytest.fail("legacy command called lifecycle repository method")

    class LegacyLeadRepository:
        def __init__(self, session: object) -> None:
            assert isinstance(session, LegacySession)
            operations.append("lead.repository")

    class LegacyTaskService:
        def __init__(self, repository: object) -> None:
            assert isinstance(repository, LegacyTaskRepository)
            operations.append("task.service")

        def create(self, data: object) -> object:
            assert cast(Any, data).status == "WAITING_CUSTOMER"
            operations.append("task.service.create")
            return task

        def get_all(self) -> list[object]:
            operations.append("task.service.get_all")
            return []

        def get(self, task_id: int) -> object:
            assert task_id == 12
            operations.append("task.service.get")
            return task

        def delete(self, candidate: object) -> None:
            assert candidate is task
            operations.append("task.service.delete")

    class LegacyLeadTaskCreationService:
        def __init__(self, lead_repository: object, task_repository: object) -> None:
            assert isinstance(lead_repository, LegacyLeadRepository)
            assert isinstance(task_repository, LegacyTaskRepository)
            operations.append("lead.task.creation.service")

        def create(
            self,
            company_id: int,
            lead_id: int,
            title: str,
            description: str | None,
        ) -> object:
            assert (company_id, lead_id, title, description) == (
                3,
                7,
                "Confirmed task",
                None,
            )
            operations.append("lead.task.creation.service.create")
            return task_cli.LeadTaskCreationResult(
                task_id=13,
                company_id=3,
                lead_id=7,
                status="TODO",
            )

    monkeypatch.setattr(
        task_cli,
        "TaskLifecycleService",
        ForbiddenTaskLifecycleService,
    )
    monkeypatch.setattr(task_cli, "SessionLocal", LegacySession)
    monkeypatch.setattr(task_cli, "TaskRepository", LegacyTaskRepository)
    monkeypatch.setattr(task_cli, "LeadRepository", LegacyLeadRepository)
    monkeypatch.setattr(task_cli, "TaskService", LegacyTaskService)
    monkeypatch.setattr(
        task_cli,
        "LeadTaskCreationService",
        LegacyLeadTaskCreationService,
    )

    for command in ("create", "create-for-lead", "list", "show", "delete"):
        help_result = runner.invoke(root_app, ["task", command, "--help"])
        assert help_result.exit_code == 0

    created = runner.invoke(
        root_app,
        [
            "task",
            "create",
            "7",
            "Legacy task",
            "--status",
            "WAITING_CUSTOMER",
        ],
    )
    confirmed = runner.invoke(
        root_app,
        [
            "task",
            "create-for-lead",
            "--company-id",
            "3",
            "--lead-id",
            "7",
            "--title",
            "Confirmed task",
            "--yes",
        ],
    )
    listed = runner.invoke(root_app, ["task", "list"])
    shown = runner.invoke(root_app, ["task", "show", "12"])
    deleted = runner.invoke(root_app, ["task", "delete", "12"])

    assert created.exit_code == 0
    assert "Task created" in created.output
    assert "Status: WAITING_CUSTOMER" in created.output
    assert confirmed.exit_code == 0
    assert confirmed.output.splitlines() == [
        "Task ID: 13",
        "Company ID: 3",
        "Lead ID: 7",
        "Status: TODO",
    ]
    assert listed.exit_code == 0
    assert listed.output.strip() == "No tasks found."
    assert shown.exit_code == 0
    assert "Title:       Legacy task" in shown.output
    assert "Status:      WAITING_CUSTOMER" in shown.output
    assert deleted.exit_code == 0
    assert deleted.output.strip() == "Task deleted"
    assert operations.count("task.service.create") == 1
    assert operations.count("lead.task.creation.service.create") == 1
    assert operations.count("task.service.get_all") == 1
    assert operations.count("task.service.get") == 1
    assert operations.count("task.repository.get") == 1
    assert operations.count("task.service.delete") == 1

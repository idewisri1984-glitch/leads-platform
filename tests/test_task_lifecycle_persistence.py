import inspect
from dataclasses import FrozenInstanceError, fields
from enum import StrEnum
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

import app.modules.task as task_module
from app.modules.task.models import Task, TaskLifecycleStatus
from app.modules.task.repository import (
    TaskLifecycleRepositoryNotFoundError,
    TaskLifecycleRepositoryTransitionError,
    TaskRepository,
    TaskStatusTransitionResult,
    _normalize_persisted_task_status,
)

INVALID_DATA = "Task lifecycle data is invalid."
NOT_FOUND = "Task was not found."
NOT_ALLOWED = "Task status transition is not allowed."


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


class OtherStatus(StrEnum):
    TODO = "TODO"


class RepositoryBaseException(BaseException):
    pass


class StrictSession:
    def __init__(
        self,
        *,
        task: Task | None = None,
        scalar_error: BaseException | None = None,
        add_error: BaseException | None = None,
        flush_error: BaseException | None = None,
    ) -> None:
        self.task = task
        self.scalar_error = scalar_error
        self.add_error = add_error
        self.flush_error = flush_error
        self.operations: list[str] = []
        self.statements: list[object] = []
        self.added: list[Task] = []

    def scalar(self, statement: object) -> Task | None:
        self.operations.append("scalar")
        self.statements.append(statement)
        if len(self.statements) > 1:
            raise AssertionError("A second scalar query was attempted.")
        if self.scalar_error is not None:
            raise self.scalar_error
        return self.task

    def add(self, task: Task) -> None:
        self.operations.append("add")
        self.added.append(task)
        if self.add_error is not None:
            raise self.add_error

    def flush(self) -> None:
        self.operations.append("flush")
        if self.flush_error is not None:
            raise self.flush_error

    def _forbidden(self, name: str) -> None:
        self.operations.append(name)
        raise AssertionError(f"Forbidden Session operation: {name}")

    def commit(self) -> None:
        self._forbidden("commit")

    def rollback(self) -> None:
        self._forbidden("rollback")

    def refresh(self, instance: object) -> None:
        self._forbidden("refresh")

    def close(self) -> None:
        self._forbidden("close")

    def begin(self) -> None:
        self._forbidden("begin")

    def begin_nested(self) -> None:
        self._forbidden("begin_nested")

    def scalars(self, statement: object) -> None:
        self._forbidden("scalars")

    def execute(self, statement: object) -> None:
        self._forbidden("execute")

    def query(self, entity: object) -> None:
        self._forbidden("query")


def repository_for(session: StrictSession) -> TaskRepository:
    return TaskRepository(cast(Session, session))


def make_task(status: object = "TODO") -> Task:
    task = Task(
        lead_id=11,
        title="Preserved title",
        description="Preserved description",
        status=status,
        due_at=None,
    )
    task.id = 23
    return task


def assert_fixed_error(
    error_type: type[BaseException],
    message: str,
    call: object,
) -> None:
    with pytest.raises(error_type) as exc_info:
        cast("object", call)()  # type: ignore[operator]
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


def test_lifecycle_enum_has_exact_members_and_values() -> None:
    assert list(TaskLifecycleStatus) == [
        TaskLifecycleStatus.TODO,
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.DONE,
        TaskLifecycleStatus.CANCELLED,
    ]
    assert [(member.name, member.value) for member in TaskLifecycleStatus] == [
        ("TODO", "TODO"),
        ("IN_PROGRESS", "IN_PROGRESS"),
        ("DONE", "DONE"),
        ("CANCELLED", "CANCELLED"),
    ]


def test_public_exports_preserve_existing_symbols_and_add_only_public_contract() -> None:
    expected_existing = {
        "LeadTaskCreationConsistencyError",
        "LeadTaskCreationError",
        "LeadTaskCreationInvalidDataError",
        "LeadTaskCreationNotFoundError",
        "LeadTaskCreationResult",
        "LeadTaskCreationService",
        "Task",
        "TaskCreate",
        "TaskRead",
        "TaskRepository",
        "TaskService",
    }
    expected_new = {
        "TaskLifecycleStatus",
        "TaskLifecycleRepositoryNotFoundError",
        "TaskLifecycleRepositoryTransitionError",
        "TaskStatusTransitionResult",
    }
    assert set(task_module.__all__) == expected_existing | expected_new
    assert not hasattr(task_module, "_normalize_persisted_task_status")
    for name in expected_existing | expected_new:
        assert hasattr(task_module, name)


def test_method_signature_is_exact() -> None:
    signature = inspect.signature(TaskRepository.set_status_for_company)
    assert list(signature.parameters) == [
        "self",
        "company_id",
        "task_id",
        "target_status",
    ]
    assert signature.parameters["company_id"].annotation is int
    assert signature.parameters["task_id"].annotation is int
    assert signature.parameters["target_status"].annotation is TaskLifecycleStatus
    assert signature.return_annotation is TaskStatusTransitionResult
    assert all(
        parameter.default is inspect.Parameter.empty for parameter in signature.parameters.values()
    )


def test_transition_result_is_frozen_authoritative_and_exact() -> None:
    task = make_task()
    result = TaskStatusTransitionResult(
        task=task,
        previous_status=TaskLifecycleStatus.TODO,
        current_status=TaskLifecycleStatus.IN_PROGRESS,
        changed=True,
    )
    assert [field.name for field in fields(result)] == [
        "task",
        "previous_status",
        "current_status",
        "changed",
    ]
    assert result.task is task
    with pytest.raises(FrozenInstanceError):
        result.changed = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "company_id",
    [True, False, 0, -1, "1", 1.0, None, object(), IntSubclass(1)],
)
def test_invalid_company_id_is_rejected_before_session_use(company_id: object) -> None:
    session = StrictSession(task=make_task())
    assert_fixed_error(
        ValueError,
        INVALID_DATA,
        lambda: repository_for(session).set_status_for_company(
            company_id,  # type: ignore[arg-type]
            1,
            TaskLifecycleStatus.TODO,
        ),
    )
    assert session.operations == []


@pytest.mark.parametrize(
    "task_id",
    [True, False, 0, -1, "1", 1.0, None, object(), IntSubclass(1)],
)
def test_invalid_task_id_is_rejected_before_session_use(task_id: object) -> None:
    session = StrictSession(task=make_task())
    assert_fixed_error(
        ValueError,
        INVALID_DATA,
        lambda: repository_for(session).set_status_for_company(
            1,
            task_id,  # type: ignore[arg-type]
            TaskLifecycleStatus.TODO,
        ),
    )
    assert session.operations == []


@pytest.mark.parametrize(
    "target",
    [
        "TODO",
        "UNKNOWN",
        True,
        1,
        None,
        object(),
        OtherStatus.TODO,
        StrSubclass("TODO"),
    ],
)
def test_invalid_target_is_rejected_before_session_use(target: object) -> None:
    session = StrictSession(task=make_task())
    assert_fixed_error(
        ValueError,
        INVALID_DATA,
        lambda: repository_for(session).set_status_for_company(
            1,
            1,
            target,  # type: ignore[arg-type]
        ),
    )
    assert session.operations == []


def test_query_is_company_scoped_fresh_and_locking() -> None:
    session = StrictSession(task=make_task())
    repository_for(session).set_status_for_company(
        7,
        23,
        TaskLifecycleStatus.TODO,
    )
    assert session.operations == ["scalar"]
    statement = session.statements[0]
    assert statement.get_execution_options()["populate_existing"] is True  # type: ignore[attr-defined]
    compiled = str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FROM tasks JOIN leads ON tasks.lead_id = leads.id" in compiled
    assert "leads.company_id = 7" in compiled
    assert "tasks.id = 23" in compiled
    assert compiled.endswith("FOR UPDATE")


def test_missing_or_cross_company_task_has_fixed_not_found_error() -> None:
    session = StrictSession(task=None)
    assert_fixed_error(
        TaskLifecycleRepositoryNotFoundError,
        NOT_FOUND,
        lambda: repository_for(session).set_status_for_company(
            1,
            2,
            TaskLifecycleStatus.TODO,
        ),
    )
    assert session.operations == ["scalar"]
    assert session.added == []


@pytest.mark.parametrize(
    ("previous", "target"),
    [
        ("TODO", TaskLifecycleStatus.IN_PROGRESS),
        ("TODO", TaskLifecycleStatus.CANCELLED),
        ("IN_PROGRESS", TaskLifecycleStatus.DONE),
        ("IN_PROGRESS", TaskLifecycleStatus.CANCELLED),
    ],
)
def test_every_changed_transition_adds_and_flushes_once(
    previous: str,
    target: TaskLifecycleStatus,
) -> None:
    task = make_task(previous)
    before = (task.id, task.lead_id, task.title, task.description, task.due_at)
    session = StrictSession(task=task)
    result = repository_for(session).set_status_for_company(1, 23, target)
    assert session.operations == ["scalar", "add", "flush"]
    assert session.added == [task]
    assert result.task is task
    assert result.previous_status is TaskLifecycleStatus(previous)
    assert result.current_status is target
    assert result.changed is True
    assert task.status == target.value
    assert (task.id, task.lead_id, task.title, task.description, task.due_at) == before


@pytest.mark.parametrize("status", list(TaskLifecycleStatus))
def test_every_idempotent_transition_performs_no_write(
    status: TaskLifecycleStatus,
) -> None:
    task = make_task(status.value)
    session = StrictSession(task=task)
    result = repository_for(session).set_status_for_company(1, 23, status)
    assert session.operations == ["scalar"]
    assert session.added == []
    assert result.task is task
    assert result.previous_status is status
    assert result.current_status is status
    assert result.changed is False
    assert task.status == status.value


@pytest.mark.parametrize(
    ("previous", "target"),
    [
        ("TODO", TaskLifecycleStatus.DONE),
        ("IN_PROGRESS", TaskLifecycleStatus.TODO),
        ("DONE", TaskLifecycleStatus.TODO),
        ("DONE", TaskLifecycleStatus.IN_PROGRESS),
        ("DONE", TaskLifecycleStatus.CANCELLED),
        ("CANCELLED", TaskLifecycleStatus.TODO),
        ("CANCELLED", TaskLifecycleStatus.IN_PROGRESS),
        ("CANCELLED", TaskLifecycleStatus.DONE),
    ],
)
def test_complete_forbidden_matrix_never_mutates(
    previous: str,
    target: TaskLifecycleStatus,
) -> None:
    task = make_task(previous)
    session = StrictSession(task=task)
    assert_fixed_error(
        TaskLifecycleRepositoryTransitionError,
        NOT_ALLOWED,
        lambda: repository_for(session).set_status_for_company(1, 23, target),
    )
    assert task.status == previous
    assert session.operations == ["scalar"]
    assert session.added == []


@pytest.mark.parametrize("value", list(TaskLifecycleStatus))
def test_normalization_accepts_lifecycle_members(value: TaskLifecycleStatus) -> None:
    assert _normalize_persisted_task_status(value) is value


@pytest.mark.parametrize("value", [member.value for member in TaskLifecycleStatus])
def test_normalization_accepts_exact_builtin_strings(value: str) -> None:
    assert _normalize_persisted_task_status(value) is TaskLifecycleStatus(value)


@pytest.mark.parametrize(
    "value",
    [
        "todo",
        " TODO",
        "TODO ",
        "",
        "WAITING_CUSTOMER",
        True,
        1,
        None,
        object(),
        StrSubclass("TODO"),
    ],
)
def test_normalization_rejects_malformed_values_without_disclosure(value: object) -> None:
    with pytest.raises(TaskLifecycleRepositoryTransitionError) as exc_info:
        _normalize_persisted_task_status(value)
    assert str(exc_info.value) == NOT_ALLOWED
    assert repr(value) not in str(exc_info.value)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("query failed"),
        SQLAlchemyError("query failed"),
        KeyboardInterrupt("query interrupted"),
        SystemExit("query exited"),
    ],
)
def test_query_errors_propagate_by_identity(error: BaseException) -> None:
    session = StrictSession(scalar_error=error)
    with pytest.raises(type(error)) as exc_info:
        repository_for(session).set_status_for_company(
            1,
            23,
            TaskLifecycleStatus.TODO,
        )
    assert exc_info.value is error
    assert session.operations == ["scalar"]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("add failed"),
        SQLAlchemyError("add failed"),
        KeyboardInterrupt("add interrupted"),
        RepositoryBaseException("add base exception"),
    ],
)
def test_add_errors_propagate_by_identity(error: BaseException) -> None:
    session = StrictSession(task=make_task(), add_error=error)
    with pytest.raises(type(error)) as exc_info:
        repository_for(session).set_status_for_company(
            1,
            23,
            TaskLifecycleStatus.IN_PROGRESS,
        )
    assert exc_info.value is error
    assert session.operations == ["scalar", "add"]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("flush failed"),
        SQLAlchemyError("flush failed"),
        IntegrityError("UPDATE tasks", {}, RuntimeError("constraint")),
        SystemExit("flush exited"),
        RepositoryBaseException("flush base exception"),
    ],
)
def test_flush_errors_propagate_by_identity(error: BaseException) -> None:
    session = StrictSession(task=make_task(), flush_error=error)
    with pytest.raises(type(error)) as exc_info:
        repository_for(session).set_status_for_company(
            1,
            23,
            TaskLifecycleStatus.IN_PROGRESS,
        )
    assert exc_info.value is error
    assert session.operations == ["scalar", "add", "flush"]


def test_legacy_creation_does_not_delegate_to_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = StrictSession()
    repository = repository_for(session)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Lifecycle method was called.")

    monkeypatch.setattr(repository, "set_status_for_company", forbidden)
    task = repository.create_for_lead(lead_id=11, title="Legacy-compatible task")
    assert task.status == "TODO"
    assert session.operations == ["add", "flush"]


def test_lifecycle_operation_has_no_output_or_logging(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = StrictSession(task=make_task())
    repository_for(session).set_status_for_company(
        1,
        23,
        TaskLifecycleStatus.TODO,
    )
    assert capsys.readouterr() == ("", "")
    assert caplog.records == []

import inspect
import traceback
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import app.modules.task as task_exports
from app.modules.task.lifecycle import (
    TaskLifecycleConsistencyError,
    TaskLifecycleError,
    TaskLifecycleInvalidDataError,
    TaskLifecycleNotFoundError,
    TaskLifecycleRepository,
    TaskLifecycleService,
    TaskLifecycleTransitionError,
)
from app.modules.task.lifecycle_schemas import TaskLifecycleResult
from app.modules.task.models import TaskLifecycleStatus
from app.modules.task.repository import (
    TaskLifecycleRepositoryNotFoundError,
    TaskLifecycleRepositoryTransitionError,
)


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


class OtherStatus(StrEnum):
    TODO = "TODO"


class ServiceBaseException(BaseException):
    pass


class TaskRecord:
    def __init__(self, task_id: object, status: object) -> None:
        self.id = task_id
        self.status = status

    @property
    def lead_id(self) -> object:
        raise AssertionError("Unsafe Task field accessed.")

    title = lead_id
    description = lead_id
    due_at = lead_id
    lead = lead_id


class StrictRepository:
    def __init__(
        self,
        result: object = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[int, int, TaskLifecycleStatus]] = []

    def set_status_for_company(
        self,
        company_id: int,
        task_id: int,
        target_status: TaskLifecycleStatus,
    ) -> Any:
        self.calls.append((company_id, task_id, target_status))
        if len(self.calls) > 1:
            raise AssertionError("Repository called twice.")
        if self.error is not None:
            raise self.error
        return self.result

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Forbidden repository API: {name}")


def transition_result(
    previous: TaskLifecycleStatus = TaskLifecycleStatus.TODO,
    current: TaskLifecycleStatus = TaskLifecycleStatus.IN_PROGRESS,
    *,
    task_id: object = 2,
    task_status: object | None = None,
    changed: object = True,
) -> object:
    return SimpleNamespace(
        task=TaskRecord(
            task_id,
            current.value if task_status is None else task_status,
        ),
        previous_status=previous,
        current_status=current,
        changed=changed,
    )


def service_for(repository: StrictRepository) -> TaskLifecycleService:
    return TaskLifecycleService(cast(TaskLifecycleRepository, repository))


def assert_consistency_error(call: Any) -> None:
    with pytest.raises(TaskLifecycleConsistencyError) as exc_info:
        call()
    assert str(exc_info.value) == "Task lifecycle state is inconsistent."
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_result_schema_is_exact_strict_frozen_and_forbids_extra() -> None:
    result = TaskLifecycleResult(
        task_id=2,
        company_id=1,
        previous_status=TaskLifecycleStatus.TODO,
        current_status=TaskLifecycleStatus.IN_PROGRESS,
        changed=True,
    )
    assert list(type(result).model_fields) == [
        "task_id",
        "company_id",
        "previous_status",
        "current_status",
        "changed",
    ]
    with pytest.raises(ValidationError):
        TaskLifecycleResult(
            task_id=True,
            company_id=1,
            previous_status=TaskLifecycleStatus.TODO,
            current_status=TaskLifecycleStatus.TODO,
            changed=False,
        )
    with pytest.raises(ValidationError):
        TaskLifecycleResult(
            task_id=2,
            company_id=1,
            previous_status=TaskLifecycleStatus.TODO,
            current_status=TaskLifecycleStatus.DONE,
            changed=True,
        )
    with pytest.raises(ValidationError):
        TaskLifecycleResult(
            task_id=2,
            company_id=1,
            previous_status=TaskLifecycleStatus.TODO,
            current_status=TaskLifecycleStatus.TODO,
            changed=True,
            extra="forbidden",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError) as exc_info:
        result.changed = False  # type: ignore[misc]
    errors = exc_info.value.errors()
    assert errors
    assert any(error["type"] == "frozen_instance" for error in errors)
    assert result.changed is True


@pytest.mark.parametrize("field", ["previous_status", "current_status"])
@pytest.mark.parametrize(
    "value",
    [
        *(status.value for status in TaskLifecycleStatus),
        "todo",
        " TODO",
        "TODO ",
        "",
        "UNKNOWN",
        StrSubclass("TODO"),
        OtherStatus.TODO,
        True,
        1,
        None,
        object(),
    ],
)
def test_result_schema_rejects_non_lifecycle_status_members(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "task_id": 2,
        "company_id": 1,
        "previous_status": TaskLifecycleStatus.TODO,
        "current_status": TaskLifecycleStatus.TODO,
        "changed": False,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        TaskLifecycleResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["previous_status", "current_status"])
@pytest.mark.parametrize("value", [status.value for status in TaskLifecycleStatus])
def test_result_schema_model_validate_rejects_raw_status_strings(
    field: str,
    value: str,
) -> None:
    values: dict[str, object] = {
        "task_id": 2,
        "company_id": 1,
        "previous_status": TaskLifecycleStatus.TODO,
        "current_status": TaskLifecycleStatus.TODO,
        "changed": False,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        TaskLifecycleResult.model_validate(values)


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (TaskLifecycleStatus.TODO, TaskLifecycleStatus.TODO),
        (TaskLifecycleStatus.TODO, TaskLifecycleStatus.IN_PROGRESS),
        (TaskLifecycleStatus.IN_PROGRESS, TaskLifecycleStatus.DONE),
        (TaskLifecycleStatus.DONE, TaskLifecycleStatus.DONE),
        (TaskLifecycleStatus.CANCELLED, TaskLifecycleStatus.CANCELLED),
    ],
)
def test_result_schema_accepts_actual_lifecycle_status_members(
    previous: TaskLifecycleStatus,
    current: TaskLifecycleStatus,
) -> None:
    result = TaskLifecycleResult(
        task_id=2,
        company_id=1,
        previous_status=previous,
        current_status=current,
        changed=previous is not current,
    )
    assert result.previous_status is previous
    assert result.current_status is current


def test_public_exports_preserve_persistence_and_add_exact_service_symbols() -> None:
    required = {
        "TaskLifecycleConsistencyError",
        "TaskLifecycleError",
        "TaskLifecycleInvalidDataError",
        "TaskLifecycleNotFoundError",
        "TaskLifecycleResult",
        "TaskLifecycleService",
        "TaskLifecycleTransitionError",
        "TaskLifecycleStatus",
        "TaskLifecycleRepositoryNotFoundError",
        "TaskLifecycleRepositoryTransitionError",
        "TaskStatusTransitionResult",
    }
    assert required <= set(task_exports.__all__)
    assert "TaskLifecycleRepository" not in task_exports.__all__
    assert "_ALLOWED_TRANSITIONS" not in task_exports.__all__


def test_service_signatures_are_exact() -> None:
    constructor = inspect.signature(TaskLifecycleService.__init__)
    transition = inspect.signature(TaskLifecycleService.transition)
    assert list(constructor.parameters) == ["self", "repository"]
    assert constructor.parameters["repository"].annotation is TaskLifecycleRepository
    assert list(transition.parameters) == [
        "self",
        "company_id",
        "task_id",
        "target_status",
    ]
    assert transition.parameters["target_status"].annotation is TaskLifecycleStatus
    assert transition.return_annotation is TaskLifecycleResult


@pytest.mark.parametrize(
    "value",
    [True, False, 0, -1, "1", 1.0, None, object(), IntSubclass(1)],
)
@pytest.mark.parametrize("field", ["company_id", "task_id"])
def test_invalid_ids_fail_before_repository(field: str, value: object) -> None:
    repository = StrictRepository(transition_result())
    values: dict[str, object] = {
        "company_id": 1,
        "task_id": 2,
        "target_status": TaskLifecycleStatus.IN_PROGRESS,
    }
    values[field] = value
    with pytest.raises(
        TaskLifecycleInvalidDataError,
        match=r"^Task lifecycle data is invalid\.$",
    ):
        service_for(repository).transition(**values)  # type: ignore[arg-type]
    assert repository.calls == []


@pytest.mark.parametrize(
    "target",
    [
        "TODO",
        "UNKNOWN",
        OtherStatus.TODO,
        True,
        1,
        None,
        object(),
        StrSubclass("TODO"),
    ],
)
def test_invalid_target_fails_before_repository(target: object) -> None:
    repository = StrictRepository(transition_result())
    with pytest.raises(TaskLifecycleInvalidDataError):
        service_for(repository).transition(1, 2, target)  # type: ignore[arg-type]
    assert repository.calls == []


def test_exact_repository_call_and_safe_result() -> None:
    repository = StrictRepository(transition_result())
    result = service_for(repository).transition(
        1,
        2,
        TaskLifecycleStatus.IN_PROGRESS,
    )
    assert repository.calls == [(1, 2, TaskLifecycleStatus.IN_PROGRESS)]
    assert result.model_dump() == {
        "task_id": 2,
        "company_id": 1,
        "previous_status": TaskLifecycleStatus.TODO,
        "current_status": TaskLifecycleStatus.IN_PROGRESS,
        "changed": True,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            TaskLifecycleRepositoryNotFoundError("unsafe marker"),
            TaskLifecycleNotFoundError,
        ),
        (
            TaskLifecycleRepositoryTransitionError("unsafe marker"),
            TaskLifecycleTransitionError,
        ),
        (TypeError("unsafe marker"), TaskLifecycleConsistencyError),
        (ValueError("unsafe marker"), TaskLifecycleConsistencyError),
    ],
)
def test_controlled_errors_are_sanitized(
    error: BaseException,
    expected: type[TaskLifecycleError],
) -> None:
    repository = StrictRepository(error=error)
    with pytest.raises(expected) as exc_info:
        service_for(repository).transition(1, 2, TaskLifecycleStatus.TODO)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    formatted = "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )
    assert "unsafe marker" not in formatted
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        IntegrityError("UPDATE tasks", {}, RuntimeError("integrity")),
        SQLAlchemyError("sqlalchemy"),
        RuntimeError("runtime"),
        OSError("os"),
        KeyboardInterrupt("interrupt"),
        SystemExit("exit"),
        ServiceBaseException("base"),
    ],
)
def test_infrastructure_errors_propagate_by_identity(error: BaseException) -> None:
    repository = StrictRepository(error=error)
    with pytest.raises(type(error)) as exc_info:
        service_for(repository).transition(1, 2, TaskLifecycleStatus.TODO)
    assert exc_info.value is error
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (TaskLifecycleStatus.TODO, TaskLifecycleStatus.IN_PROGRESS),
        (TaskLifecycleStatus.TODO, TaskLifecycleStatus.CANCELLED),
        (TaskLifecycleStatus.IN_PROGRESS, TaskLifecycleStatus.DONE),
        (TaskLifecycleStatus.IN_PROGRESS, TaskLifecycleStatus.CANCELLED),
    ],
)
def test_all_changed_results_are_accepted(
    previous: TaskLifecycleStatus,
    current: TaskLifecycleStatus,
) -> None:
    repository = StrictRepository(transition_result(previous, current))
    result = service_for(repository).transition(1, 2, current)
    assert result.previous_status is previous
    assert result.current_status is current
    assert result.changed is True


@pytest.mark.parametrize("status", list(TaskLifecycleStatus))
def test_all_idempotent_results_are_accepted(status: TaskLifecycleStatus) -> None:
    repository = StrictRepository(transition_result(status, status, changed=False))
    result = service_for(repository).transition(1, 2, status)
    assert result.changed is False
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    "result",
    [
        None,
        object(),
        SimpleNamespace(
            task=None,
            previous_status=TaskLifecycleStatus.TODO,
            current_status=TaskLifecycleStatus.TODO,
            changed=False,
        ),
        transition_result(task_id=True),
        transition_result(task_id=3),
        transition_result(task_status="todo"),
        transition_result(task_status=StrSubclass("IN_PROGRESS")),
        transition_result(changed=1),
        transition_result(changed=False),
        transition_result(
            TaskLifecycleStatus.TODO,
            TaskLifecycleStatus.IN_PROGRESS,
            task_status=TaskLifecycleStatus.DONE,
        ),
        SimpleNamespace(
            task=TaskRecord(2, "IN_PROGRESS"),
            previous_status="TODO",
            current_status=TaskLifecycleStatus.IN_PROGRESS,
            changed=True,
        ),
    ],
)
def test_malformed_repository_results_are_rejected(result: object) -> None:
    repository = StrictRepository(result)
    assert_consistency_error(
        lambda: service_for(repository).transition(
            1,
            2,
            TaskLifecycleStatus.IN_PROGRESS,
        )
    )
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (TaskLifecycleStatus.TODO, TaskLifecycleStatus.DONE),
        (TaskLifecycleStatus.IN_PROGRESS, TaskLifecycleStatus.TODO),
        (TaskLifecycleStatus.DONE, TaskLifecycleStatus.TODO),
        (TaskLifecycleStatus.DONE, TaskLifecycleStatus.IN_PROGRESS),
        (TaskLifecycleStatus.DONE, TaskLifecycleStatus.CANCELLED),
        (TaskLifecycleStatus.CANCELLED, TaskLifecycleStatus.TODO),
        (TaskLifecycleStatus.CANCELLED, TaskLifecycleStatus.IN_PROGRESS),
        (TaskLifecycleStatus.CANCELLED, TaskLifecycleStatus.DONE),
    ],
)
def test_all_forbidden_repository_pairs_are_rejected(
    previous: TaskLifecycleStatus,
    current: TaskLifecycleStatus,
) -> None:
    repository = StrictRepository(transition_result(previous, current))
    assert_consistency_error(lambda: service_for(repository).transition(1, 2, current))


def test_task_status_accepts_exact_string_and_enum() -> None:
    for representation in [
        "IN_PROGRESS",
        TaskLifecycleStatus.IN_PROGRESS,
    ]:
        result = transition_result(task_status=representation)
        assert (
            service_for(StrictRepository(result))
            .transition(1, 2, TaskLifecycleStatus.IN_PROGRESS)
            .current_status
            is TaskLifecycleStatus.IN_PROGRESS
        )


def test_service_has_no_output_or_logging(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    service_for(StrictRepository(transition_result())).transition(
        1,
        2,
        TaskLifecycleStatus.IN_PROGRESS,
    )
    assert capsys.readouterr() == ("", "")
    assert caplog.records == []

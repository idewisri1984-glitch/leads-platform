from typing import Protocol

from pydantic import ValidationError

from app.modules.task.lifecycle_schemas import TaskLifecycleResult
from app.modules.task.models import TaskLifecycleStatus
from app.modules.task.repository import (
    TaskLifecycleRepositoryNotFoundError,
    TaskLifecycleRepositoryTransitionError,
    TaskStatusTransitionResult,
)

_INVALID_DATA = "Task lifecycle data is invalid."
_NOT_FOUND = "Task was not found."
_TRANSITION_NOT_ALLOWED = "Task status transition is not allowed."
_INCONSISTENT_STATE = "Task lifecycle state is inconsistent."

_ALLOWED_TRANSITIONS = {
    TaskLifecycleStatus.TODO: {
        TaskLifecycleStatus.TODO,
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.CANCELLED,
    },
    TaskLifecycleStatus.IN_PROGRESS: {
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.DONE,
        TaskLifecycleStatus.CANCELLED,
    },
    TaskLifecycleStatus.DONE: {TaskLifecycleStatus.DONE},
    TaskLifecycleStatus.CANCELLED: {TaskLifecycleStatus.CANCELLED},
}

_MISSING = object()


class TaskLifecycleError(ValueError):
    pass


class TaskLifecycleInvalidDataError(TaskLifecycleError):
    pass


class TaskLifecycleNotFoundError(TaskLifecycleError):
    pass


class TaskLifecycleTransitionError(TaskLifecycleError):
    pass


class TaskLifecycleConsistencyError(TaskLifecycleError):
    pass


class TaskLifecycleRepository(Protocol):
    def set_status_for_company(
        self,
        company_id: int,
        task_id: int,
        target_status: TaskLifecycleStatus,
    ) -> TaskStatusTransitionResult: ...


class TaskLifecycleService:
    def __init__(self, repository: TaskLifecycleRepository) -> None:
        self.repository = repository

    def transition(
        self,
        company_id: int,
        task_id: int,
        target_status: TaskLifecycleStatus,
    ) -> TaskLifecycleResult:
        if (
            type(company_id) is not int
            or company_id <= 0
            or type(task_id) is not int
            or task_id <= 0
            or type(target_status) is not TaskLifecycleStatus
        ):
            raise TaskLifecycleInvalidDataError(_INVALID_DATA)

        translated_error: TaskLifecycleError | None = None
        repository_result: object = None
        try:
            repository_result = self.repository.set_status_for_company(
                company_id,
                task_id,
                target_status,
            )
        except TaskLifecycleRepositoryNotFoundError:
            translated_error = TaskLifecycleNotFoundError(_NOT_FOUND)
        except TaskLifecycleRepositoryTransitionError:
            translated_error = TaskLifecycleTransitionError(_TRANSITION_NOT_ALLOWED)
        except (TypeError, ValueError):
            translated_error = TaskLifecycleConsistencyError(_INCONSISTENT_STATE)
        if translated_error is not None:
            raise translated_error from None

        validated: (
            tuple[
                int,
                TaskLifecycleStatus,
                TaskLifecycleStatus,
                bool,
            ]
            | None
        ) = None
        validation_failed = False
        try:
            validated = _validate_repository_result(
                repository_result,
                task_id,
                target_status,
            )
        except (AttributeError, TypeError, ValueError):
            validation_failed = True
        if validation_failed or validated is None:
            raise TaskLifecycleConsistencyError(_INCONSISTENT_STATE) from None

        returned_task_id, previous_status, current_status, changed = validated
        result: TaskLifecycleResult | None = None
        result_failed = False
        try:
            result = TaskLifecycleResult(
                task_id=returned_task_id,
                company_id=company_id,
                previous_status=previous_status,
                current_status=current_status,
                changed=changed,
            )
        except ValidationError:
            result_failed = True
        if result_failed or result is None:
            raise TaskLifecycleConsistencyError(_INCONSISTENT_STATE) from None
        return result


def _validate_repository_result(
    result: object,
    task_id: int,
    target_status: TaskLifecycleStatus,
) -> tuple[int, TaskLifecycleStatus, TaskLifecycleStatus, bool]:
    if result is None:
        raise ValueError

    task = getattr(result, "task", _MISSING)
    previous_status = getattr(result, "previous_status", _MISSING)
    current_status = getattr(result, "current_status", _MISSING)
    changed = getattr(result, "changed", _MISSING)
    if task is _MISSING or task is None:
        raise ValueError

    returned_task_id = getattr(task, "id", _MISSING)
    returned_status = getattr(task, "status", _MISSING)
    if (
        type(returned_task_id) is not int
        or returned_task_id <= 0
        or returned_task_id != task_id
        or type(previous_status) is not TaskLifecycleStatus
        or type(current_status) is not TaskLifecycleStatus
        or current_status is not target_status
        or type(changed) is not bool
        or changed is not (previous_status is not current_status)
        or current_status not in _ALLOWED_TRANSITIONS[previous_status]
        or not _status_matches_target(returned_status, target_status)
    ):
        raise ValueError

    return returned_task_id, previous_status, current_status, changed


def _status_matches_target(
    status: object,
    target_status: TaskLifecycleStatus,
) -> bool:
    return status is target_status or (type(status) is str and status == target_status.value)

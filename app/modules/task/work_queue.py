from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, cast

from app.modules.task.models import TaskLifecycleStatus
from app.modules.task.work_queue_schemas import (
    TaskWorkQueueBucket,
    TaskWorkQueueItem,
    TaskWorkQueueResult,
)

_INVALID_DATA = "Task work queue data is invalid."
_INCONSISTENT_STATE = "Task work queue state is inconsistent."
_MALFORMED = object()


class TaskWorkQueueError(ValueError):
    pass


class TaskWorkQueueInvalidDataError(TaskWorkQueueError):
    pass


class TaskWorkQueueConsistencyError(TaskWorkQueueError):
    pass


@dataclass(frozen=True)
class TaskWorkQueueRecord:
    task_id: int
    lead_id: int
    title: str
    status: TaskLifecycleStatus | str
    due_at: datetime | None


class TaskWorkQueueRepository(Protocol):
    def list_work_queue_for_company(
        self,
        company_id: int,
        as_of: datetime,
        upcoming_until: datetime,
    ) -> list[TaskWorkQueueRecord]: ...


def _read_records(
    repository: TaskWorkQueueRepository,
    company_id: int,
    as_of: datetime,
    upcoming_until: datetime,
) -> object:
    try:
        return repository.list_work_queue_for_company(
            company_id,
            as_of,
            upcoming_until,
        )
    except (TypeError, ValueError):
        return _MALFORMED


def _record_values(
    record: object,
) -> tuple[object, object, object, object, object] | None:
    try:
        selected = cast(TaskWorkQueueRecord, record)
        return (
            selected.task_id,
            selected.lead_id,
            selected.title,
            selected.status,
            selected.due_at,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _active_status(value: object) -> TaskLifecycleStatus | None:
    if type(value) is TaskLifecycleStatus and value in {
        TaskLifecycleStatus.TODO,
        TaskLifecycleStatus.IN_PROGRESS,
    }:
        return value
    if type(value) is str:
        if value == TaskLifecycleStatus.TODO.value:
            return TaskLifecycleStatus.TODO
        if value == TaskLifecycleStatus.IN_PROGRESS.value:
            return TaskLifecycleStatus.IN_PROGRESS
    return None


def _build_result(
    company_id: int,
    as_of: datetime,
    upcoming_until: datetime,
    records: object,
) -> TaskWorkQueueResult | None:
    if type(records) is not list:
        return None

    items: list[TaskWorkQueueItem] = []
    seen: set[int] = set()
    try:
        for record in records:
            values = _record_values(record)
            if values is None:
                return None
            task_id, lead_id, title, raw_status, due_at = values
            if (
                type(task_id) is not int
                or task_id <= 0
                or task_id in seen
                or type(lead_id) is not int
                or lead_id <= 0
                or type(title) is not str
                or len(title) > 255
                or (
                    due_at is not None
                    and (type(due_at) is not datetime or due_at.tzinfo is not None)
                )
            ):
                return None
            status = _active_status(raw_status)
            if status is None:
                return None
            if due_at is None:
                bucket = TaskWorkQueueBucket.UNSCHEDULED
            elif due_at < as_of:
                bucket = TaskWorkQueueBucket.OVERDUE
            elif due_at <= upcoming_until:
                bucket = TaskWorkQueueBucket.UPCOMING
            else:
                return None
            seen.add(task_id)
            items.append(
                TaskWorkQueueItem(
                    task_id=task_id,
                    lead_id=lead_id,
                    title=title,
                    status=status,
                    due_at=due_at,
                    bucket=bucket,
                )
            )
        bucket_rank = {
            TaskWorkQueueBucket.OVERDUE: 0,
            TaskWorkQueueBucket.UPCOMING: 1,
            TaskWorkQueueBucket.UNSCHEDULED: 2,
        }
        items.sort(
            key=lambda item: (
                bucket_rank[item.bucket],
                (item.due_at if item.due_at is not None else datetime.max),
                0 if item.status is TaskLifecycleStatus.IN_PROGRESS else 1,
                item.task_id,
            )
        )
        frozen_items = tuple(items)
        return TaskWorkQueueResult(
            company_id=company_id,
            as_of=as_of,
            upcoming_until=upcoming_until,
            overdue_count=sum(item.bucket is TaskWorkQueueBucket.OVERDUE for item in frozen_items),
            upcoming_count=sum(
                item.bucket is TaskWorkQueueBucket.UPCOMING for item in frozen_items
            ),
            unscheduled_count=sum(
                item.bucket is TaskWorkQueueBucket.UNSCHEDULED for item in frozen_items
            ),
            items=frozen_items,
        )
    except (TypeError, ValueError):
        return None


class TaskWorkQueueService:
    def __init__(self, repository: TaskWorkQueueRepository) -> None:
        self.repository = repository

    def get_queue(
        self,
        company_id: int,
        as_of: datetime,
        days: int = 7,
    ) -> TaskWorkQueueResult:
        if (
            type(company_id) is not int
            or company_id <= 0
            or type(as_of) is not datetime
            or as_of.tzinfo is not None
            or type(days) is not int
            or not 1 <= days <= 30
        ):
            raise TaskWorkQueueInvalidDataError(_INVALID_DATA)
        try:
            upcoming_until = as_of + timedelta(days=days)
        except OverflowError:
            raise TaskWorkQueueInvalidDataError(_INVALID_DATA) from None

        records = _read_records(
            self.repository,
            company_id,
            as_of,
            upcoming_until,
        )
        if records is _MALFORMED:
            raise TaskWorkQueueConsistencyError(_INCONSISTENT_STATE)
        result = _build_result(company_id, as_of, upcoming_until, records)
        if result is None:
            raise TaskWorkQueueConsistencyError(_INCONSISTENT_STATE)
        return result

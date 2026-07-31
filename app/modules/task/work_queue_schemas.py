from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.modules.task.models import TaskLifecycleStatus

PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]


class TaskWorkQueueBucket(StrEnum):
    OVERDUE = "OVERDUE"
    UPCOMING = "UPCOMING"
    UNSCHEDULED = "UNSCHEDULED"


def _require_exact_datetime(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is not None:
        raise ValueError("Task work queue datetime is invalid.")
    return value


def _item_order_key(
    item: "TaskWorkQueueItem",
) -> tuple[int, datetime, int, int]:
    bucket_rank = {
        TaskWorkQueueBucket.OVERDUE: 0,
        TaskWorkQueueBucket.UPCOMING: 1,
        TaskWorkQueueBucket.UNSCHEDULED: 2,
    }[item.bucket]
    status_rank = 0 if item.status is TaskLifecycleStatus.IN_PROGRESS else 1
    due_at = item.due_at if item.due_at is not None else datetime.max
    return (bucket_rank, due_at, status_rank, item.task_id)


class TaskWorkQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: PositiveStrictInt
    lead_id: PositiveStrictInt
    title: StrictStr
    status: TaskLifecycleStatus
    due_at: datetime | None
    bucket: TaskWorkQueueBucket

    @field_validator("task_id", "lead_id", mode="before")
    @classmethod
    def require_exact_positive_id(cls, value: object) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("Task work queue identifier is invalid.")
        return value

    @field_validator("title", mode="before")
    @classmethod
    def require_exact_title(cls, value: object) -> str:
        if type(value) is not str or len(value) > 255:
            raise ValueError("Task work queue title is invalid.")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def require_active_status(cls, value: object) -> TaskLifecycleStatus:
        if type(value) is not TaskLifecycleStatus or value not in {
            TaskLifecycleStatus.TODO,
            TaskLifecycleStatus.IN_PROGRESS,
        }:
            raise ValueError("Task work queue status is invalid.")
        return value

    @field_validator("due_at", mode="before")
    @classmethod
    def require_optional_naive_datetime(cls, value: object) -> datetime | None:
        if value is None:
            return None
        return _require_exact_datetime(value)

    @field_validator("bucket", mode="before")
    @classmethod
    def require_bucket(cls, value: object) -> TaskWorkQueueBucket:
        if type(value) is not TaskWorkQueueBucket:
            raise ValueError("Task work queue bucket is invalid.")
        return value


class TaskWorkQueueResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    company_id: PositiveStrictInt
    as_of: datetime
    upcoming_until: datetime
    overdue_count: NonNegativeStrictInt
    upcoming_count: NonNegativeStrictInt
    unscheduled_count: NonNegativeStrictInt
    items: tuple[TaskWorkQueueItem, ...]

    @field_validator("company_id", mode="before")
    @classmethod
    def require_exact_company_id(cls, value: object) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("Task work queue Company ID is invalid.")
        return value

    @field_validator("as_of", "upcoming_until", mode="before")
    @classmethod
    def require_naive_datetime(cls, value: object) -> datetime:
        return _require_exact_datetime(value)

    @field_validator(
        "overdue_count",
        "upcoming_count",
        "unscheduled_count",
        mode="before",
    )
    @classmethod
    def require_exact_count(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("Task work queue count is invalid.")
        return value

    @field_validator("items", mode="before")
    @classmethod
    def require_exact_items_tuple(
        cls,
        value: object,
    ) -> tuple[TaskWorkQueueItem, ...]:
        if type(value) is not tuple or any(type(item) is not TaskWorkQueueItem for item in value):
            raise ValueError("Task work queue items are invalid.")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.upcoming_until <= self.as_of:
            raise ValueError("Task work queue window is invalid.")
        if len({item.task_id for item in self.items}) != len(self.items):
            raise ValueError("Task work queue Task IDs are not unique.")

        counts = {
            TaskWorkQueueBucket.OVERDUE: 0,
            TaskWorkQueueBucket.UPCOMING: 0,
            TaskWorkQueueBucket.UNSCHEDULED: 0,
        }
        for item in self.items:
            if item.due_at is None:
                expected = TaskWorkQueueBucket.UNSCHEDULED
            elif item.due_at < self.as_of:
                expected = TaskWorkQueueBucket.OVERDUE
            elif item.due_at <= self.upcoming_until:
                expected = TaskWorkQueueBucket.UPCOMING
            else:
                raise ValueError("Task is outside the work queue window.")
            if item.bucket is not expected:
                raise ValueError("Task work queue bucket is inconsistent.")
            counts[item.bucket] += 1

        if (
            counts[TaskWorkQueueBucket.OVERDUE] != self.overdue_count
            or counts[TaskWorkQueueBucket.UPCOMING] != self.upcoming_count
            or counts[TaskWorkQueueBucket.UNSCHEDULED] != self.unscheduled_count
            or tuple(sorted(self.items, key=_item_order_key)) != self.items
        ):
            raise ValueError("Task work queue result is inconsistent.")
        return self

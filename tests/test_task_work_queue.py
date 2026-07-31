from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from inspect import signature
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import app.modules.task as task_module
from app.modules.task import (
    TaskLifecycleStatus,
    TaskWorkQueueBucket,
    TaskWorkQueueConsistencyError,
    TaskWorkQueueInvalidDataError,
    TaskWorkQueueItem,
    TaskWorkQueueResult,
    TaskWorkQueueService,
)
from app.modules.task.work_queue import (
    TaskWorkQueueRecord,
    TaskWorkQueueRepository,
)

AS_OF = datetime(2026, 7, 31, 9)


class IntSubclass(int):
    pass


class StringSubclass(str):
    pass


class DateTimeSubclass(datetime):
    pass


class TupleSubclass(tuple[TaskWorkQueueItem, ...]):
    pass


class ListSubclass(list[TaskWorkQueueRecord]):
    pass


class CustomIterable:
    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(())


class CustomBaseException(BaseException):
    pass


def item(
    task_id: int = 1,
    *,
    status: TaskLifecycleStatus = TaskLifecycleStatus.TODO,
    due_at: datetime | None = None,
    bucket: TaskWorkQueueBucket = TaskWorkQueueBucket.UNSCHEDULED,
    title: str = "Call buyer",
) -> TaskWorkQueueItem:
    return TaskWorkQueueItem(
        task_id=task_id,
        lead_id=7,
        title=title,
        status=status,
        due_at=due_at,
        bucket=bucket,
    )


def result(*items: TaskWorkQueueItem) -> TaskWorkQueueResult:
    return TaskWorkQueueResult(
        company_id=3,
        as_of=AS_OF,
        upcoming_until=AS_OF + timedelta(days=7),
        overdue_count=sum(x.bucket is TaskWorkQueueBucket.OVERDUE for x in items),
        upcoming_count=sum(x.bucket is TaskWorkQueueBucket.UPCOMING for x in items),
        unscheduled_count=sum(x.bucket is TaskWorkQueueBucket.UNSCHEDULED for x in items),
        items=items,
    )


def test_public_contract_is_exact() -> None:
    assert list(TaskWorkQueueBucket) == [
        TaskWorkQueueBucket.OVERDUE,
        TaskWorkQueueBucket.UPCOMING,
        TaskWorkQueueBucket.UNSCHEDULED,
    ]
    assert list(TaskWorkQueueItem.model_fields) == [
        "task_id",
        "lead_id",
        "title",
        "status",
        "due_at",
        "bucket",
    ]
    assert list(TaskWorkQueueResult.model_fields) == [
        "company_id",
        "as_of",
        "upcoming_until",
        "overdue_count",
        "upcoming_count",
        "unscheduled_count",
        "items",
    ]
    expected = {
        "TaskWorkQueueBucket",
        "TaskWorkQueueConsistencyError",
        "TaskWorkQueueError",
        "TaskWorkQueueInvalidDataError",
        "TaskWorkQueueItem",
        "TaskWorkQueueResult",
        "TaskWorkQueueService",
    }
    assert expected <= set(task_module.__all__)
    assert len(task_module.__all__) == 29
    assert not hasattr(task_module, "TaskWorkQueueRecord")
    assert not hasattr(task_module, "TaskWorkQueueRepository")


def test_models_are_frozen_and_forbid_extras() -> None:
    selected = item()
    with pytest.raises((ValidationError, FrozenInstanceError)):
        selected.title = "changed"
    with pytest.raises(ValidationError):
        TaskWorkQueueItem.model_validate(
            {**selected.model_dump(), "bucket": selected.bucket, "extra": True}
        )
    with pytest.raises(ValidationError):
        TaskWorkQueueResult.model_validate(
            {**result(selected).model_dump(), "items": (selected,), "extra": True}
        )


@pytest.mark.parametrize("bad_id", [True, 0, -1, 1.0, "1", None])
def test_item_requires_exact_positive_ids(bad_id: object) -> None:
    with pytest.raises(ValidationError):
        TaskWorkQueueItem(
            task_id=cast(int, bad_id),
            lead_id=7,
            title="x",
            status=TaskLifecycleStatus.TODO,
            due_at=None,
            bucket=TaskWorkQueueBucket.UNSCHEDULED,
        )


def test_item_title_and_enums_are_strict() -> None:
    assert len(item(title="x" * 255).title) == 255
    for values in (
        {"title": "x" * 256},
        {"title": 3},
        {"status": "TODO"},
        {"status": TaskLifecycleStatus.DONE},
        {"bucket": "UNSCHEDULED"},
        {"due_at": datetime.now(UTC)},
    ):
        base = {
            "task_id": 1,
            "lead_id": 7,
            "title": "x",
            "status": TaskLifecycleStatus.TODO,
            "due_at": None,
            "bucket": TaskWorkQueueBucket.UNSCHEDULED,
        }
        with pytest.raises(ValidationError):
            TaskWorkQueueItem(**{**base, **values})


def test_result_requires_tuple_counts_unique_buckets_and_order() -> None:
    overdue = item(
        due_at=AS_OF - timedelta(seconds=1),
        bucket=TaskWorkQueueBucket.OVERDUE,
    )
    upcoming = item(
        2,
        due_at=AS_OF,
        bucket=TaskWorkQueueBucket.UPCOMING,
    )
    assert result(overdue, upcoming).upcoming_count == 1
    invalid_values = (
        {"items": [overdue, upcoming]},
        {"overdue_count": 2},
        {"items": (overdue, overdue)},
        {"items": (upcoming, overdue)},
        {"upcoming_until": AS_OF},
    )
    base = result(overdue, upcoming).model_dump()
    for replacement in invalid_values:
        if "items" not in replacement:
            replacement["items"] = (overdue, upcoming)
        with pytest.raises(ValidationError):
            TaskWorkQueueResult(**{**base, **replacement})


class RecordingRepository:
    def __init__(self, records: object) -> None:
        self.records = records
        self.calls: list[tuple[int, datetime, datetime]] = []

    def list_work_queue_for_company(
        self,
        company_id: int,
        as_of: datetime,
        upcoming_until: datetime,
    ) -> list[TaskWorkQueueRecord]:
        self.calls.append((company_id, as_of, upcoming_until))
        return cast(list[TaskWorkQueueRecord], self.records)


def test_service_signature_and_protocol_are_narrow() -> None:
    assert list(signature(TaskWorkQueueService.get_queue).parameters) == [
        "self",
        "company_id",
        "as_of",
        "days",
    ]
    assert {name for name in TaskWorkQueueRepository.__dict__ if not name.startswith("_")} == {
        "list_work_queue_for_company"
    }


@pytest.mark.parametrize(
    ("company_id", "as_of", "days"),
    [
        (True, AS_OF, 7),
        (0, AS_OF, 7),
        (3, datetime.now(UTC), 7),
        (3, AS_OF, True),
        (3, AS_OF, 0),
        (3, AS_OF, 31),
    ],
)
def test_service_validates_before_repository(
    company_id: object,
    as_of: object,
    days: object,
) -> None:
    repository = RecordingRepository([])
    with pytest.raises(
        TaskWorkQueueInvalidDataError,
        match="^Task work queue data is invalid\\.$",
    ):
        TaskWorkQueueService(repository).get_queue(
            cast(int, company_id),
            cast(datetime, as_of),
            cast(int, days),
        )
    assert repository.calls == []


def test_service_rejects_datetime_overflow_before_repository() -> None:
    repository = RecordingRepository([])
    with pytest.raises(TaskWorkQueueInvalidDataError):
        TaskWorkQueueService(repository).get_queue(3, datetime.max, 1)
    assert repository.calls == []


def test_service_calls_once_classifies_and_orders() -> None:
    repository = RecordingRepository(
        [
            TaskWorkQueueRecord(5, 7, "u todo", "TODO", None),
            TaskWorkQueueRecord(4, 7, "due todo", "TODO", AS_OF),
            TaskWorkQueueRecord(3, 7, "due progress", "IN_PROGRESS", AS_OF),
            TaskWorkQueueRecord(2, 7, "old", TaskLifecycleStatus.TODO, AS_OF - timedelta(days=1)),
            TaskWorkQueueRecord(1, 7, "u progress", "IN_PROGRESS", None),
        ]
    )
    selected = TaskWorkQueueService(repository).get_queue(3, AS_OF)
    assert repository.calls == [(3, AS_OF, AS_OF + timedelta(days=7))]
    assert [x.task_id for x in selected.items] == [2, 3, 4, 1, 5]
    assert (
        selected.overdue_count,
        selected.upcoming_count,
        selected.unscheduled_count,
    ) == (1, 2, 2)


@pytest.mark.parametrize(
    "records",
    [
        None,
        (),
        [SimpleNamespace()],
        [TaskWorkQueueRecord(0, 7, "x", "TODO", None)],
        [TaskWorkQueueRecord(1, 7, "x", "DONE", None)],
        [TaskWorkQueueRecord(1, 7, "x", "WAITING_CUSTOMER", None)],
        [TaskWorkQueueRecord(1, 7, "x", "TODO", datetime.now(UTC))],
        [
            TaskWorkQueueRecord(1, 7, "x", "TODO", None),
            TaskWorkQueueRecord(1, 7, "x", "TODO", None),
        ],
        [TaskWorkQueueRecord(1, 7, "x", "TODO", AS_OF + timedelta(days=8))],
    ],
)
def test_service_rejects_untrusted_repository_output(records: object) -> None:
    with pytest.raises(
        TaskWorkQueueConsistencyError,
        match="^Task work queue state is inconsistent\\.$",
    ) as raised:
        TaskWorkQueueService(RecordingRepository(records)).get_queue(3, AS_OF)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("error", [RuntimeError("x"), OSError("x")])
def test_infrastructure_errors_preserve_identity(error: Exception) -> None:
    class FailingRepository(RecordingRepository):
        def list_work_queue_for_company(
            self, company_id: int, as_of: datetime, upcoming_until: datetime
        ) -> list[TaskWorkQueueRecord]:
            raise error

    with pytest.raises(type(error)) as raised:
        TaskWorkQueueService(FailingRepository([])).get_queue(3, AS_OF)
    assert raised.value is error


def test_repository_value_error_is_sanitized() -> None:
    class FailingRepository(RecordingRepository):
        def list_work_queue_for_company(
            self, company_id: int, as_of: datetime, upcoming_until: datetime
        ) -> list[TaskWorkQueueRecord]:
            raise ValueError("secret title and SQL")

    with pytest.raises(TaskWorkQueueConsistencyError) as raised:
        TaskWorkQueueService(FailingRepository([])).get_queue(3, AS_OF)
    assert str(raised.value) == "Task work queue state is inconsistent."
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", IntSubclass(1)),
        ("lead_id", IntSubclass(7)),
        ("title", StringSubclass("title")),
        ("due_at", DateTimeSubclass(2026, 7, 31, 9)),
    ],
)
@pytest.mark.parametrize("validation", ["direct", "model_validate"])
def test_safe_item_rejects_strict_subclasses(
    field: str,
    value: object,
    validation: str,
) -> None:
    values = {
        "task_id": 1,
        "lead_id": 7,
        "title": "title",
        "status": TaskLifecycleStatus.TODO,
        "due_at": None,
        "bucket": TaskWorkQueueBucket.UNSCHEDULED,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        if validation == "direct":
            TaskWorkQueueItem(**values)  # type: ignore[arg-type]
        else:
            TaskWorkQueueItem.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_id", IntSubclass(3)),
        ("as_of", DateTimeSubclass(2026, 7, 31, 9)),
        ("upcoming_until", DateTimeSubclass(2026, 8, 7, 9)),
    ],
)
@pytest.mark.parametrize("validation", ["direct", "model_validate"])
def test_safe_result_rejects_strict_subclasses(
    field: str,
    value: object,
    validation: str,
) -> None:
    values = {
        "company_id": 3,
        "as_of": AS_OF,
        "upcoming_until": AS_OF + timedelta(days=7),
        "overdue_count": 0,
        "upcoming_count": 0,
        "unscheduled_count": 0,
        "items": (),
    }
    values[field] = value
    with pytest.raises(ValidationError):
        if validation == "direct":
            TaskWorkQueueResult(**values)  # type: ignore[arg-type]
        else:
            TaskWorkQueueResult.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "TODO"),
        ("status", "IN_PROGRESS"),
        ("status", StringSubclass("TODO")),
        ("status", TaskWorkQueueBucket.OVERDUE),
        ("status", 1),
        ("status", True),
        ("status", None),
        ("status", object()),
        ("bucket", "OVERDUE"),
        ("bucket", "UPCOMING"),
        ("bucket", "UNSCHEDULED"),
        ("bucket", StringSubclass("OVERDUE")),
        ("bucket", TaskLifecycleStatus.TODO),
        ("bucket", 1),
        ("bucket", True),
        ("bucket", None),
        ("bucket", object()),
    ],
)
@pytest.mark.parametrize("validation", ["direct", "model_validate"])
def test_safe_item_rejects_raw_and_unrelated_enum_values(
    field: str,
    value: object,
    validation: str,
) -> None:
    values = {
        "task_id": 1,
        "lead_id": 7,
        "title": "title",
        "status": TaskLifecycleStatus.TODO,
        "due_at": None,
        "bucket": TaskWorkQueueBucket.UNSCHEDULED,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        if validation == "direct":
            TaskWorkQueueItem(**values)  # type: ignore[arg-type]
        else:
            TaskWorkQueueItem.model_validate(values)


@pytest.mark.parametrize(
    "items",
    [
        [],
        set(),
        frozenset(),
        (item for item in ()),
        CustomIterable(),
        TupleSubclass(),
        1,
        None,
    ],
)
def test_safe_result_accepts_only_exact_tuple(items: object) -> None:
    with pytest.raises(ValidationError):
        TaskWorkQueueResult(
            company_id=3,
            as_of=AS_OF,
            upcoming_until=AS_OF + timedelta(days=7),
            overdue_count=0,
            upcoming_count=0,
            unscheduled_count=0,
            items=cast(tuple[TaskWorkQueueItem, ...], items),
        )


@pytest.mark.parametrize(
    "outer",
    [
        None,
        (),
        TupleSubclass(),
        (record for record in ()),
        iter(()),
        set(),
        frozenset(),
        {},
        {}.values(),
        "records",
        b"records",
        ListSubclass(),
        CustomIterable(),
        SimpleNamespace(all=lambda: []),
        1,
        object(),
    ],
)
def test_service_rejects_every_non_exact_list_outer_result(outer: object) -> None:
    repository = RecordingRepository(outer)
    with pytest.raises(TaskWorkQueueConsistencyError) as raised:
        TaskWorkQueueService(repository).get_queue(3, AS_OF)
    assert str(raised.value) == "Task work queue state is inconsistent."
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert repository.calls == [(3, AS_OF, AS_OF + timedelta(days=7))]


@pytest.mark.parametrize(
    "record",
    [
        SimpleNamespace(lead_id=7, title="x", status="TODO", due_at=None),
        SimpleNamespace(task_id=1, title="x", status="TODO", due_at=None),
        SimpleNamespace(task_id=1, lead_id=7, status="TODO", due_at=None),
        SimpleNamespace(task_id=1, lead_id=7, title="x", due_at=None),
        SimpleNamespace(task_id=1, lead_id=7, title="x", status="TODO"),
        TaskWorkQueueRecord(True, 7, "x", "TODO", None),
        TaskWorkQueueRecord(0, 7, "x", "TODO", None),
        TaskWorkQueueRecord(-1, 7, "x", "TODO", None),
        TaskWorkQueueRecord(IntSubclass(1), 7, "x", "TODO", None),
        TaskWorkQueueRecord(1, 7, StringSubclass("x"), "TODO", None),
        TaskWorkQueueRecord(1, 7, "x" * 256, "TODO", None),
        TaskWorkQueueRecord(1, 7, "x", "todo", None),
        TaskWorkQueueRecord(1, 7, "x", " TODO", None),
        TaskWorkQueueRecord(1, 7, "x", "DONE", None),
        TaskWorkQueueRecord(1, 7, "x", "CANCELLED", None),
        TaskWorkQueueRecord(1, 7, "x", "WAITING_CUSTOMER", None),
        TaskWorkQueueRecord(1, 7, "x", "TODO", datetime.now(UTC)),
        TaskWorkQueueRecord(1, 7, "x", "TODO", DateTimeSubclass(2026, 7, 31, 9)),
        TaskWorkQueueRecord(1, 7, "x", "TODO", AS_OF + timedelta(days=8)),
    ],
)
def test_service_rejects_complete_malformed_record_matrix(record: object) -> None:
    repository = RecordingRepository([record])
    with pytest.raises(TaskWorkQueueConsistencyError) as raised:
        TaskWorkQueueService(repository).get_queue(3, AS_OF)
    assert str(raised.value) == "Task work queue state is inconsistent."
    assert repository.calls == [(3, AS_OF, AS_OF + timedelta(days=7))]


@pytest.mark.parametrize(
    "error",
    [
        SQLAlchemyError("sqlalchemy"),
        IntegrityError("statement", {}, Exception("driver")),
        KeyboardInterrupt(),
        SystemExit(),
        GeneratorExit(),
        CustomBaseException(),
    ],
)
def test_all_infrastructure_baseexceptions_preserve_identity(error: BaseException) -> None:
    class FailingRepository(RecordingRepository):
        def list_work_queue_for_company(
            self, company_id: int, as_of: datetime, upcoming_until: datetime
        ) -> list[TaskWorkQueueRecord]:
            raise error

    with pytest.raises(type(error)) as raised:
        TaskWorkQueueService(FailingRepository([])).get_queue(3, AS_OF)
    assert raised.value is error

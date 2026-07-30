import inspect
import traceback
from dataclasses import dataclass
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.modules import task as task_exports
from app.modules.task import (
    LeadTaskCreationConsistencyError,
    LeadTaskCreationError,
    LeadTaskCreationInvalidDataError,
    LeadTaskCreationNotFoundError,
    LeadTaskCreationResult,
    LeadTaskCreationService,
)
from app.modules.task.lead_task_creation import (
    LeadTaskCreationLeadRepository,
    LeadTaskCreationTaskRepository,
)


@dataclass
class LeadRecord:
    id: Any
    company_id: Any


@dataclass
class TaskRecord:
    id: Any
    lead_id: Any
    title: Any
    description: Any
    status: Any = "TODO"
    due_at: Any = None


class LeadRepositoryFake:
    def __init__(
        self,
        *,
        lead: object = LeadRecord(2, 1),
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.lead = lead
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[tuple[int, int]] = []

    def get_for_company(self, company_id: int, lead_id: int) -> object:
        self.operations.append("lead_lookup")
        self.calls.append((company_id, lead_id))
        if self.error is not None:
            raise self.error
        return self.lead

    def get(self, lead_id: int) -> None:
        raise AssertionError("Generic Lead get must not be called.")

    def get_all(self) -> None:
        raise AssertionError("Lead list must not be called.")

    def get_by_company(self, company_id: int) -> None:
        raise AssertionError("Lead list must not be called.")


class TaskRepositoryFake:
    def __init__(
        self,
        *,
        tasks: list[object] | None = None,
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.tasks = list(tasks) if tasks is not None else [TaskRecord(3, 2, "Follow up", None)]
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[dict[str, object]] = []

    def create_for_lead(
        self,
        *,
        lead_id: int,
        title: str,
        description: str | None = None,
    ) -> object:
        self.operations.append("task_creation")
        self.calls.append(
            {
                "lead_id": lead_id,
                "title": title,
                "description": description,
            }
        )
        if self.error is not None:
            raise self.error
        return self.tasks.pop(0)

    def create(self, **values: object) -> None:
        raise AssertionError(f"Generic Task create called with {values!r}")

    def get(self, task_id: int) -> None:
        raise AssertionError("Task lookup must not be called.")

    def get_by_lead(self, lead_id: int) -> None:
        raise AssertionError("Task lookup must not be called.")

    def commit(self) -> None:
        raise AssertionError("Commit must not be called.")

    def rollback(self) -> None:
        raise AssertionError("Rollback must not be called.")

    def close(self) -> None:
        raise AssertionError("Close must not be called.")


def make_service(
    *,
    lead: object = LeadRecord(2, 1),
    lead_error: BaseException | None = None,
    tasks: list[object] | None = None,
    task_error: BaseException | None = None,
) -> tuple[LeadTaskCreationService, LeadRepositoryFake, TaskRepositoryFake]:
    operations: list[str] = []
    lead_repository = LeadRepositoryFake(
        lead=lead,
        error=lead_error,
        operations=operations,
    )
    task_repository = TaskRepositoryFake(
        tasks=tasks,
        error=task_error,
        operations=operations,
    )
    service = LeadTaskCreationService(
        cast(LeadTaskCreationLeadRepository, lead_repository),
        cast(LeadTaskCreationTaskRepository, task_repository),
    )
    return service, lead_repository, task_repository


def test_result_schema_is_strict_frozen_and_text_free() -> None:
    result = LeadTaskCreationResult(
        task_id=3,
        company_id=1,
        lead_id=2,
        status="TODO",
    )

    assert result.model_dump() == {
        "task_id": 3,
        "company_id": 1,
        "lead_id": 2,
        "status": "TODO",
    }
    assert set(LeadTaskCreationResult.model_fields) == {
        "task_id",
        "company_id",
        "lead_id",
        "status",
    }
    assert not {"title", "description", "due_at"} & set(LeadTaskCreationResult.model_fields)
    with pytest.raises(ValidationError):
        result.task_id = 4


@pytest.mark.parametrize("field", ["task_id", "company_id", "lead_id"])
@pytest.mark.parametrize("value", [True, 0, -1])
def test_result_schema_rejects_invalid_identifiers(field: str, value: object) -> None:
    values: dict[str, object] = {
        "task_id": 3,
        "company_id": 1,
        "lead_id": 2,
        "status": "TODO",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        LeadTaskCreationResult.model_validate(values)


def test_result_schema_rejects_wrong_status_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LeadTaskCreationResult(
            task_id=3,
            company_id=1,
            lead_id=2,
            status="DONE",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        LeadTaskCreationResult.model_validate(
            {
                "task_id": 3,
                "company_id": 1,
                "lead_id": 2,
                "status": "TODO",
                "title": "private",
            }
        )


def test_service_signature_is_exact() -> None:
    signature = inspect.signature(LeadTaskCreationService.create)

    assert list(signature.parameters) == [
        "self",
        "company_id",
        "lead_id",
        "title",
        "description",
    ]
    assert signature.parameters["description"].default is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_id", True),
        ("company_id", False),
        ("company_id", 0),
        ("company_id", -1),
        ("company_id", "1"),
        ("company_id", 1.0),
        ("company_id", None),
        ("company_id", object()),
        ("company_id", type("CompanyIntSubclass", (int,), {})(1)),
        ("lead_id", True),
        ("lead_id", False),
        ("lead_id", 0),
        ("lead_id", -1),
        ("lead_id", "2"),
        ("lead_id", 2.0),
        ("lead_id", None),
        ("lead_id", object()),
        ("lead_id", type("LeadIntSubclass", (int,), {})(2)),
    ],
)
def test_invalid_identifiers_fail_before_repository_calls(
    field: str,
    value: object,
) -> None:
    service, lead_repository, task_repository = make_service()
    values: dict[str, object] = {
        "company_id": 1,
        "lead_id": 2,
        "title": "Follow up",
    }
    values[field] = value

    with pytest.raises(
        LeadTaskCreationInvalidDataError,
        match=r"^Task creation data is invalid\.$",
    ):
        service.create(**values)  # type: ignore[arg-type]

    assert lead_repository.operations == []
    assert lead_repository.calls == []
    assert task_repository.calls == []


@pytest.mark.parametrize(
    "title",
    [
        None,
        True,
        1,
        b"title",
        object(),
        type("TitleSubclass", (str,), {})("x"),
        "",
        "  ",
        "x" * 256,
    ],
)
def test_invalid_titles_fail_before_repository_calls(title: object) -> None:
    service, lead_repository, task_repository = make_service()

    with pytest.raises(
        LeadTaskCreationInvalidDataError,
        match=r"^Task creation data is invalid\.$",
    ):
        service.create(1, 2, title)  # type: ignore[arg-type]

    assert lead_repository.operations == []
    assert task_repository.calls == []


@pytest.mark.parametrize(
    "description",
    [True, 1, 1.0, b"description", object(), type("DescriptionSubclass", (str,), {})("x")],
)
def test_invalid_descriptions_fail_before_repository_calls(
    description: object,
) -> None:
    service, lead_repository, task_repository = make_service()

    with pytest.raises(LeadTaskCreationInvalidDataError):
        service.create(1, 2, "Follow up", description)  # type: ignore[arg-type]

    assert lead_repository.operations == []
    assert task_repository.calls == []


def test_success_preserves_exact_values_order_and_safe_result() -> None:
    title = "  Follow up  "
    description = "  Call tomorrow  "
    task = TaskRecord(3, 2, title, description)
    service, lead_repository, task_repository = make_service(tasks=[task])

    result = service.create(1, 2, title, description)

    assert lead_repository.operations == ["lead_lookup", "task_creation"]
    assert lead_repository.calls == [(1, 2)]
    assert task_repository.calls == [
        {
            "lead_id": 2,
            "title": title,
            "description": description,
        }
    ]
    assert result == LeadTaskCreationResult(
        task_id=3,
        company_id=1,
        lead_id=2,
        status="TODO",
    )
    assert title not in repr(result)
    assert description not in repr(result)


def test_title_boundary_and_empty_description_are_preserved() -> None:
    title = "x" * 255
    service, _, task_repository = make_service(tasks=[TaskRecord(3, 2, title, "")])

    service.create(1, 2, title, "")

    assert task_repository.calls[0]["title"] == title
    assert task_repository.calls[0]["description"] == ""


def test_missing_lead_is_hidden_and_task_is_not_called() -> None:
    service, lead_repository, task_repository = make_service(lead=None)

    with pytest.raises(
        LeadTaskCreationNotFoundError,
        match=r"^Lead was not found\.$",
    ):
        service.create(1, 2, "Follow up")

    assert lead_repository.calls == [(1, 2)]
    assert task_repository.calls == []


@pytest.mark.parametrize(
    "lead",
    [
        object(),
        LeadRecord(None, 1),
        LeadRecord(True, 1),
        LeadRecord(3, 1),
        type("MissingCompany", (), {"id": 2})(),
        LeadRecord(2, True),
        LeadRecord(2, 3),
    ],
)
def test_malformed_or_mismatched_lead_is_rejected(lead: object) -> None:
    service, _, task_repository = make_service(lead=lead)

    with pytest.raises(
        LeadTaskCreationConsistencyError,
        match=r"^Task creation state is inconsistent\.$",
    ):
        service.create(1, 2, "Follow up")

    assert task_repository.calls == []


@pytest.mark.parametrize(
    "task",
    [
        None,
        object(),
        TaskRecord(None, 2, "Follow up", None),
        TaskRecord(True, 2, "Follow up", None),
        TaskRecord(3, True, "Follow up", None),
        TaskRecord(3, 4, "Follow up", None),
        TaskRecord(3, 2, b"Follow up", None),
        TaskRecord(3, 2, "Changed", None),
        TaskRecord(3, 2, "Follow up", object()),
        TaskRecord(3, 2, "Follow up", "Changed"),
        TaskRecord(3, 2, "Follow up", None, "DONE"),
        TaskRecord(3, 2, "Follow up", None, True),
        TaskRecord(3, 2, "Follow up", None, "TODO", object()),
    ],
)
def test_malformed_or_mismatched_task_is_rejected(task: object) -> None:
    service, _, _ = make_service(tasks=[task])

    with pytest.raises(
        LeadTaskCreationConsistencyError,
        match=r"^Task creation state is inconsistent\.$",
    ):
        service.create(1, 2, "Follow up")


@pytest.mark.parametrize("boundary", ["lead", "task"])
@pytest.mark.parametrize(
    "error",
    [TypeError("private repository marker"), ValueError("private repository marker")],
)
def test_controlled_repository_errors_are_fully_sanitized(
    boundary: str,
    error: BaseException,
) -> None:
    kwargs = {f"{boundary}_error": error}
    service, _, _ = make_service(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(LeadTaskCreationConsistencyError) as captured:
        service.create(1, 2, "Follow up")

    assert str(captured.value) == "Task creation state is inconsistent."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    formatted = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert "private repository marker" not in formatted


@pytest.mark.parametrize("boundary", ["lead", "task"])
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("runtime"),
        SQLAlchemyError("sqlalchemy"),
        IntegrityError("insert", {}, RuntimeError("integrity")),
        KeyboardInterrupt("interrupt"),
        SystemExit("exit"),
    ],
)
def test_infrastructure_errors_propagate_with_identity(
    boundary: str,
    error: BaseException,
) -> None:
    kwargs = {f"{boundary}_error": error}
    service, _, _ = make_service(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(type(error)) as captured:
        service.create(1, 2, "Follow up")

    assert captured.value is error


def test_repeated_success_is_non_idempotent() -> None:
    service, _, task_repository = make_service(
        tasks=[
            TaskRecord(3, 2, "Follow up", None),
            TaskRecord(4, 2, "Follow up", None),
        ]
    )

    first = service.create(1, 2, "Follow up")
    second = service.create(1, 2, "Follow up")

    assert first.task_id == 3
    assert second.task_id == 4
    assert len(task_repository.calls) == 2


def test_service_has_no_output_or_logging(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _ = make_service()

    service.create(1, 2, "Follow up")

    assert capsys.readouterr() == ("", "")
    assert caplog.records == []


def test_public_exports_are_exact_and_protocols_remain_internal() -> None:
    approved = {
        "LeadTaskCreationConsistencyError",
        "LeadTaskCreationError",
        "LeadTaskCreationInvalidDataError",
        "LeadTaskCreationNotFoundError",
        "LeadTaskCreationResult",
        "LeadTaskCreationService",
    }

    assert approved <= set(task_exports.__all__)
    assert not any("Protocol" in name or name.endswith("Repository") for name in approved)
    assert "LeadTaskCreationLeadRepository" not in task_exports.__all__
    assert "LeadTaskCreationTaskRepository" not in task_exports.__all__
    assert issubclass(LeadTaskCreationInvalidDataError, LeadTaskCreationError)
    assert issubclass(LeadTaskCreationNotFoundError, LeadTaskCreationError)
    assert issubclass(LeadTaskCreationConsistencyError, LeadTaskCreationError)

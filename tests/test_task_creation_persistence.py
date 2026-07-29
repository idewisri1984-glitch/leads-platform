import inspect
from collections.abc import Callable
from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository

INVALID_DATA = "Task creation data is invalid."


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


class StrictSession:
    def __init__(
        self,
        *,
        add_error: BaseException | None = None,
        flush_error: BaseException | None = None,
    ) -> None:
        self.add_error = add_error
        self.flush_error = flush_error
        self.operations: list[str] = []
        self.added: list[Task] = []

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

    def scalar(self, statement: object) -> None:
        self._forbidden("scalar")

    def scalars(self, statement: object) -> None:
        self._forbidden("scalars")

    def execute(self, statement: object) -> None:
        self._forbidden("execute")

    def get(self, entity: object, identifier: object) -> None:
        self._forbidden("get")

    def query(self, entity: object) -> None:
        self._forbidden("query")


def repository_for(session: StrictSession) -> TaskRepository:
    return TaskRepository(cast(Session, session))


def assert_fixed_validation_error(call: Callable[[], object]) -> None:
    with pytest.raises(ValueError) as exc_info:
        call()

    assert str(exc_info.value) == INVALID_DATA
    assert repr(exc_info.value) == f"ValueError('{INVALID_DATA}')"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_create_for_lead_signature_is_narrow_and_keyword_only() -> None:
    signature = inspect.signature(TaskRepository.create_for_lead)

    assert list(signature.parameters) == [
        "self",
        "lead_id",
        "title",
        "description",
    ]
    assert signature.parameters["self"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert signature.parameters["lead_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["title"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["description"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["description"].default is None
    assert "status" not in signature.parameters
    assert "due_at" not in signature.parameters
    assert "company_id" not in signature.parameters
    assert "contact_id" not in signature.parameters
    assert "project_id" not in signature.parameters


def test_create_for_lead_maps_exact_values_and_uses_only_add_then_flush() -> None:
    session = StrictSession()
    repository = repository_for(session)

    task = repository.create_for_lead(
        lead_id=17,
        title="  Follow up  ",
        description="  Preserve this description  ",
    )

    assert task.lead_id == 17
    assert task.title == "  Follow up  "
    assert task.description == "  Preserve this description  "
    assert task.status == "TODO"
    assert task.due_at is None
    assert session.operations == ["add", "flush"]
    assert session.added == [task]
    assert session.added[0] is task


def test_create_for_lead_defaults_description_to_none() -> None:
    session = StrictSession()

    task = repository_for(session).create_for_lead(lead_id=1, title="Follow up")

    assert task.description is None


def test_create_for_lead_preserves_empty_description() -> None:
    session = StrictSession()

    task = repository_for(session).create_for_lead(
        lead_id=1,
        title="Follow up",
        description="",
    )

    assert task.description == ""


def test_create_for_lead_accepts_and_preserves_255_character_title() -> None:
    session = StrictSession()
    title = "x" * 255

    task = repository_for(session).create_for_lead(lead_id=1, title=title)

    assert task.title == title


@pytest.mark.parametrize(
    "lead_id",
    [
        True,
        False,
        0,
        -1,
        "1",
        1.0,
        None,
        object(),
        IntSubclass(1),
    ],
)
def test_create_for_lead_rejects_invalid_lead_ids_without_session_mutation(
    lead_id: object,
) -> None:
    session = StrictSession()

    assert_fixed_validation_error(
        lambda: repository_for(session).create_for_lead(
            lead_id=lead_id,  # type: ignore[arg-type]
            title="Follow up",
        )
    )

    assert session.operations == []
    assert session.added == []


@pytest.mark.parametrize(
    "title",
    [
        None,
        True,
        1,
        b"title",
        object(),
        StrSubclass("Follow up"),
        "",
        " \t\r\n ",
        "x" * 256,
    ],
)
def test_create_for_lead_rejects_invalid_titles_without_session_mutation(
    title: object,
) -> None:
    session = StrictSession()

    assert_fixed_validation_error(
        lambda: repository_for(session).create_for_lead(
            lead_id=1,
            title=title,  # type: ignore[arg-type]
        )
    )

    assert session.operations == []
    assert session.added == []


@pytest.mark.parametrize(
    "description",
    [True, False, 1, 1.0, b"description", object(), StrSubclass("description")],
)
def test_create_for_lead_rejects_invalid_descriptions_without_session_mutation(
    description: object,
) -> None:
    session = StrictSession()

    assert_fixed_validation_error(
        lambda: repository_for(session).create_for_lead(
            lead_id=1,
            title="Follow up",
            description=description,  # type: ignore[arg-type]
        )
    )

    assert session.operations == []
    assert session.added == []


def test_validation_finishes_before_task_construction_or_session_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = StrictSession()

    def forbidden_task_construction(**values: object) -> Task:
        raise AssertionError(f"Task was constructed with {values!r}")

    monkeypatch.setattr(
        "app.modules.task.repository.Task",
        forbidden_task_construction,
    )

    assert_fixed_validation_error(
        lambda: repository_for(session).create_for_lead(
            lead_id=0,
            title="",
            description=object(),  # type: ignore[arg-type]
        )
    )
    assert session.operations == []


def test_validation_error_does_not_expose_invalid_values() -> None:
    session = StrictSession()
    secret = "private-title-value"

    with pytest.raises(ValueError) as exc_info:
        repository_for(session).create_for_lead(
            lead_id=0,
            title=secret,
            description="private-description-value",
        )

    assert str(exc_info.value) == INVALID_DATA
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_create_for_lead_does_not_delegate_to_legacy_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = StrictSession()
    repository = repository_for(session)

    def forbidden_legacy_create(**values: object) -> Task:
        raise AssertionError(f"Legacy create called with {values!r}")

    monkeypatch.setattr(repository, "create", forbidden_legacy_create)

    task = repository.create_for_lead(lead_id=1, title="Follow up")

    assert task.status == "TODO"
    assert session.operations == ["add", "flush"]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("add failed"),
        KeyboardInterrupt("add interrupted"),
        SystemExit("add exited"),
    ],
)
def test_add_failures_propagate_unchanged_without_cleanup(
    error: BaseException,
) -> None:
    session = StrictSession(add_error=error)

    with pytest.raises(type(error)) as exc_info:
        repository_for(session).create_for_lead(lead_id=1, title="Follow up")

    assert exc_info.value is error
    assert session.operations == ["add"]
    assert len(session.added) == 1


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("flush failed"),
        SQLAlchemyError("sqlalchemy flush failed"),
        IntegrityError(
            "INSERT INTO tasks (...) VALUES (...)",
            {},
            RuntimeError("foreign key failed"),
        ),
        KeyboardInterrupt("flush interrupted"),
        SystemExit("flush exited"),
    ],
)
def test_flush_failures_propagate_unchanged_without_cleanup(
    error: BaseException,
) -> None:
    session = StrictSession(flush_error=error)

    with pytest.raises(type(error)) as exc_info:
        repository_for(session).create_for_lead(lead_id=1, title="Follow up")

    assert exc_info.value is error
    assert session.operations == ["add", "flush"]
    assert len(session.added) == 1


def test_repeated_calls_create_distinct_tasks_without_lookup_or_reuse() -> None:
    session = StrictSession()
    repository = repository_for(session)

    first = repository.create_for_lead(
        lead_id=1,
        title="Follow up",
        description="Call",
    )
    second = repository.create_for_lead(
        lead_id=1,
        title="Follow up",
        description="Call",
    )

    assert first is not second
    assert session.added == [first, second]
    assert session.operations == ["add", "flush", "add", "flush"]

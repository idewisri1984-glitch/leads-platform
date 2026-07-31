from datetime import UTC, datetime, timedelta
from inspect import signature
from typing import cast

import pytest
from typer.testing import CliRunner

import app.cli.task as task_cli
from app.cli.main import app as root_app
from app.modules.task import (
    TaskLifecycleStatus,
    TaskWorkQueueBucket,
    TaskWorkQueueConsistencyError,
    TaskWorkQueueItem,
    TaskWorkQueueResult,
)

runner = CliRunner()
AS_OF = datetime(2026, 7, 31, 9)


def queue_result(title: str = "Call buyer") -> TaskWorkQueueResult:
    selected = TaskWorkQueueItem(
        task_id=12,
        lead_id=7,
        title=title,
        status=TaskLifecycleStatus.IN_PROGRESS,
        due_at=AS_OF - timedelta(days=1),
        bucket=TaskWorkQueueBucket.OVERDUE,
    )
    return TaskWorkQueueResult(
        company_id=3,
        as_of=AS_OF,
        upcoming_until=AS_OF + timedelta(days=7),
        overdue_count=1,
        upcoming_count=0,
        unscheduled_count=0,
        items=(selected,),
    )


class StrictSession:
    def __init__(self, close_error: BaseException | None = None) -> None:
        self.close_calls = 0
        self.close_error = close_error

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"read-only queue used Session.{name}")


class Service:
    def __init__(
        self,
        selected: TaskWorkQueueResult | BaseException,
    ) -> None:
        self.selected = selected
        self.calls: list[tuple[int, datetime, int]] = []

    def get_queue(self, company_id: int, as_of: datetime, days: int) -> TaskWorkQueueResult:
        self.calls.append((company_id, as_of, days))
        if isinstance(self.selected, BaseException):
            raise self.selected
        return self.selected


def execute_with(
    selected: TaskWorkQueueResult | BaseException,
    *,
    session: StrictSession | None = None,
) -> tuple[task_cli.TaskWorkQueueCommandOutcome, StrictSession, Service]:
    chosen_session = session or StrictSession()
    service = Service(selected)
    outcome = task_cli.execute_task_work_queue(
        company_id=3,
        as_of=AS_OF,
        days=7,
        session_factory=cast(task_cli.SessionFactory, lambda: chosen_session),
        task_repository_factory=cast(task_cli.TaskRepositoryFactory, lambda value: object()),
        service_factory=cast(task_cli.TaskWorkQueueServiceFactory, lambda repository: service),
    )
    return outcome, chosen_session, service


def test_command_and_executor_signatures_are_exact() -> None:
    assert {command.name for command in task_cli.app.registered_commands} == {
        "create",
        "create-for-lead",
        "list",
        "show",
        "delete",
        "start",
        "complete",
        "cancel",
        "queue",
    }
    assert list(signature(task_cli.execute_task_work_queue).parameters) == [
        "company_id",
        "as_of",
        "days",
        "session_factory",
        "task_repository_factory",
        "service_factory",
    ]
    assert list(task_cli.TaskWorkQueueCommandOutcome.__dataclass_fields__) == [
        "exit_code",
        "result",
        "error_message",
    ]


def test_queue_help_has_only_approved_options() -> None:
    result = runner.invoke(root_app, ["task", "queue", "--help"])
    assert result.exit_code == 0
    for option in ("--company-id", "--as-of", "--days", "--help"):
        assert option in result.output
    for option in ("--yes", "--status", "--timezone", "--now", "--limit", "--force"):
        assert option not in result.output


@pytest.mark.parametrize(
    ("company_id", "as_of", "days"),
    [
        (True, AS_OF, 7),
        (0, AS_OF, 7),
        (3, datetime.now(UTC), 7),
        (3, AS_OF, True),
        (3, AS_OF, 31),
        (3, datetime.max, 1),
    ],
)
def test_invalid_executor_input_precedes_session(
    company_id: object, as_of: object, days: object
) -> None:
    calls = 0

    def forbidden() -> StrictSession:
        nonlocal calls
        calls += 1
        raise AssertionError

    outcome = task_cli.execute_task_work_queue(
        company_id=cast(int, company_id),
        as_of=cast(datetime, as_of),
        days=cast(int, days),
        session_factory=cast(task_cli.SessionFactory, forbidden),
    )
    assert outcome == task_cli.TaskWorkQueueCommandOutcome(
        exit_code=1,
        error_message="Task work queue data is invalid.",
    )
    assert calls == 0


def test_success_wires_once_closes_once_and_preserves_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = queue_result()
    outcome, session, service = execute_with(selected)
    assert outcome.result is selected
    assert service.calls == [(3, AS_OF, 7)]
    assert session.close_calls == 1
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            TaskWorkQueueConsistencyError("secret"),
            "Task work queue state is inconsistent.",
        ),
        (RuntimeError("secret"), "Task work queue failed."),
    ],
)
def test_failures_are_fixed_and_close_once(error: BaseException, message: str) -> None:
    outcome, session, _ = execute_with(error)
    assert outcome == task_cli.TaskWorkQueueCommandOutcome(exit_code=1, error_message=message)
    assert session.close_calls == 1


def test_close_failure_hides_result_without_rollback() -> None:
    outcome, session, _ = execute_with(
        queue_result(), session=StrictSession(RuntimeError("close secret"))
    )
    assert outcome.result is None
    assert outcome.error_message == "Task work queue failed."
    assert session.close_calls == 1


def test_baseexception_identity_and_cleanup_suppression() -> None:
    primary = KeyboardInterrupt()
    session = StrictSession(SystemExit())
    with pytest.raises(KeyboardInterrupt) as raised:
        execute_with(primary, session=session)
    assert raised.value is primary
    assert session.close_calls == 1


@pytest.mark.parametrize(
    "value",
    ["", " 2026-07-31T09:00:00", "2026-07-31", "bad", "2026-07-31T09:00:00Z"],
)
def test_invalid_as_of_is_rejected_before_session(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setattr(
        task_cli,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("Session constructed")),
    )
    result = runner.invoke(
        root_app,
        ["task", "queue", "--company-id", "3", "--as-of", value],
    )
    assert result.exit_code == 1
    assert result.output == "Task work queue data is invalid.\n"


def test_nonempty_output_is_exact_and_json_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = queue_result('Call\\buyer\nnext\t\x1b"')
    monkeypatch.setattr(
        task_cli,
        "execute_task_work_queue",
        lambda **kwargs: task_cli.TaskWorkQueueCommandOutcome(exit_code=0, result=selected),
    )
    result = runner.invoke(
        root_app,
        ["task", "queue", "--company-id", "3", "--as-of", "2026-07-31T09:00:00"],
    )
    assert result.exit_code == 0
    assert result.output == (
        "Company ID: 3\n"
        "As Of: 2026-07-31T09:00:00\n"
        "Upcoming Through: 2026-08-07T09:00:00\n"
        "Overdue: 1\n"
        "Upcoming: 0\n"
        "Unscheduled: 0\n"
        "Tasks:\n"
        "OVERDUE | Task ID: 12 | Lead ID: 7 | Status: IN_PROGRESS | "
        'Due At: 2026-07-30T09:00:00 | Title: "Call\\\\buyer\\nnext\\t\\u001b\\""\n'
    )


def test_empty_output_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = TaskWorkQueueResult(
        company_id=3,
        as_of=AS_OF,
        upcoming_until=AS_OF + timedelta(days=7),
        overdue_count=0,
        upcoming_count=0,
        unscheduled_count=0,
        items=(),
    )
    monkeypatch.setattr(
        task_cli,
        "execute_task_work_queue",
        lambda **kwargs: task_cli.TaskWorkQueueCommandOutcome(exit_code=0, result=selected),
    )
    result = runner.invoke(
        root_app,
        ["task", "queue", "--company-id", "3", "--as-of", "2026-07-31T09:00:00"],
    )
    assert result.output == (
        "Company ID: 3\n"
        "As Of: 2026-07-31T09:00:00\n"
        "Upcoming Through: 2026-08-07T09:00:00\n"
        "Overdue: 0\n"
        "Upcoming: 0\n"
        "Unscheduled: 0\n"
        "No active tasks in work queue.\n"
    )

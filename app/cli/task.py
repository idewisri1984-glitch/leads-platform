import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta

import typer
from sqlalchemy.orm import Session

from app.cli._lazy_dependencies import SessionLocal
from app.modules.lead import LeadRepository
from app.modules.task import (
    LeadTaskCreationConsistencyError,
    LeadTaskCreationInvalidDataError,
    LeadTaskCreationNotFoundError,
    LeadTaskCreationResult,
    LeadTaskCreationService,
    TaskCreate,
    TaskLifecycleConsistencyError,
    TaskLifecycleInvalidDataError,
    TaskLifecycleNotFoundError,
    TaskLifecycleResult,
    TaskLifecycleService,
    TaskLifecycleStatus,
    TaskLifecycleTransitionError,
    TaskRepository,
    TaskService,
    TaskWorkQueueConsistencyError,
    TaskWorkQueueInvalidDataError,
    TaskWorkQueueResult,
    TaskWorkQueueService,
)

app = typer.Typer(help="Task management commands.")

SessionFactory = Callable[[], Session]
LeadRepositoryFactory = Callable[[Session], LeadRepository]
TaskRepositoryFactory = Callable[[Session], TaskRepository]
TaskWorkQueueServiceFactory = Callable[[TaskRepository], TaskWorkQueueService]
LeadTaskCreationServiceFactory = Callable[
    [LeadRepository, TaskRepository],
    LeadTaskCreationService,
]
TaskLifecycleServiceFactory = Callable[
    [TaskRepository],
    TaskLifecycleService,
]

_CONFIRMATION_REQUIRED = "Task creation requires --yes."
_INVALID_DATA = "Task creation data is invalid."
_NOT_FOUND = "Lead was not found."
_INCONSISTENT_STATE = "Task creation state is inconsistent."
_CREATION_FAILED = "Task creation failed."
_LIFECYCLE_CONFIRMATION_REQUIRED = "Task lifecycle transition requires --yes."
_LIFECYCLE_INVALID_DATA = "Task lifecycle data is invalid."
_LIFECYCLE_NOT_FOUND = "Task was not found."
_LIFECYCLE_TRANSITION_NOT_ALLOWED = "Task status transition is not allowed."
_LIFECYCLE_INCONSISTENT_STATE = "Task lifecycle state is inconsistent."
_LIFECYCLE_FAILED = "Task lifecycle transition failed."


@dataclass(frozen=True)
class LeadTaskCreationCommandOutcome:
    exit_code: int
    result: LeadTaskCreationResult | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class TaskLifecycleCommandOutcome:
    exit_code: int
    result: TaskLifecycleResult | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class TaskWorkQueueCommandOutcome:
    exit_code: int
    result: TaskWorkQueueResult | None = None
    error_message: str | None = None


_WORK_QUEUE_INVALID_DATA = "Task work queue data is invalid."
_WORK_QUEUE_INCONSISTENT_STATE = "Task work queue state is inconsistent."
_WORK_QUEUE_FAILED = "Task work queue failed."


def _valid_work_queue_input(
    company_id: object,
    as_of: object,
    days: object,
) -> bool:
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(as_of) is not datetime
        or as_of.tzinfo is not None
        or type(days) is not int
        or not 1 <= days <= 30
    ):
        return False
    try:
        as_of + timedelta(days=days)
    except OverflowError:
        return False
    return True


def execute_task_work_queue(
    *,
    company_id: int,
    as_of: datetime,
    days: int = 7,
    session_factory: SessionFactory | None = None,
    task_repository_factory: TaskRepositoryFactory | None = None,
    service_factory: TaskWorkQueueServiceFactory | None = None,
) -> TaskWorkQueueCommandOutcome:
    if not _valid_work_queue_input(company_id, as_of, days):
        return TaskWorkQueueCommandOutcome(
            exit_code=1,
            error_message=_WORK_QUEUE_INVALID_DATA,
        )

    session: Session | None = None
    outcome: TaskWorkQueueCommandOutcome
    try:
        session = (session_factory or SessionLocal)()
        repository = (task_repository_factory or TaskRepository)(session)
        service = (service_factory or TaskWorkQueueService)(repository)
        result = service.get_queue(company_id, as_of, days)
        outcome = TaskWorkQueueCommandOutcome(exit_code=0, result=result)
    except TaskWorkQueueInvalidDataError:
        outcome = TaskWorkQueueCommandOutcome(
            exit_code=1,
            error_message=_WORK_QUEUE_INVALID_DATA,
        )
    except TaskWorkQueueConsistencyError:
        outcome = TaskWorkQueueCommandOutcome(
            exit_code=1,
            error_message=_WORK_QUEUE_INCONSISTENT_STATE,
        )
    except Exception:
        outcome = TaskWorkQueueCommandOutcome(
            exit_code=1,
            error_message=_WORK_QUEUE_FAILED,
        )
    except BaseException:
        if session is not None:
            with suppress(BaseException):
                session.close()
        raise

    if session is not None:
        try:
            session.close()
        except Exception:
            return TaskWorkQueueCommandOutcome(
                exit_code=1,
                error_message=_WORK_QUEUE_FAILED,
            )
    return outcome


def _parse_work_queue_as_of(value: object) -> datetime | None:
    if type(value) is not str or not value or value != value.strip() or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if type(parsed) is not datetime or parsed.tzinfo is not None:
        return None
    return parsed


def _print_task_work_queue(result: TaskWorkQueueResult) -> None:
    typer.echo(f"Company ID: {result.company_id}")
    typer.echo(f"As Of: {result.as_of.isoformat(timespec='seconds')}")
    typer.echo(f"Upcoming Through: {result.upcoming_until.isoformat(timespec='seconds')}")
    typer.echo(f"Overdue: {result.overdue_count}")
    typer.echo(f"Upcoming: {result.upcoming_count}")
    typer.echo(f"Unscheduled: {result.unscheduled_count}")
    if not result.items:
        typer.echo("No active tasks in work queue.")
        return
    typer.echo("Tasks:")
    for item in result.items:
        due_at = item.due_at.isoformat(timespec="seconds") if item.due_at is not None else "-"
        title = json.dumps(item.title, ensure_ascii=False)
        typer.echo(
            f"{item.bucket.value} | Task ID: {item.task_id} | "
            f"Lead ID: {item.lead_id} | Status: {item.status.value} | "
            f"Due At: {due_at} | Title: {title}"
        )


@app.command("queue")
def task_work_queue(
    company_id: int = typer.Option(help="Company ID."),
    as_of: str = typer.Option(help="Naive ISO datetime."),
    days: int = typer.Option(7, help="Upcoming window in days (1-30)."),
) -> None:
    parsed_as_of = _parse_work_queue_as_of(as_of)
    if parsed_as_of is None:
        typer.echo(_WORK_QUEUE_INVALID_DATA, err=True)
        raise typer.Exit(1)
    outcome = execute_task_work_queue(
        company_id=company_id,
        as_of=parsed_as_of,
        days=days,
    )
    if outcome.error_message is not None:
        typer.echo(outcome.error_message, err=True)
    if outcome.result is not None:
        _print_task_work_queue(outcome.result)
    raise typer.Exit(outcome.exit_code)


def execute_task_lifecycle_transition(
    *,
    company_id: int,
    task_id: int,
    target_status: TaskLifecycleStatus,
    yes: bool,
    session_factory: SessionFactory | None = None,
    task_repository_factory: TaskRepositoryFactory | None = None,
    service_factory: TaskLifecycleServiceFactory | None = None,
) -> TaskLifecycleCommandOutcome:
    if yes is not True:
        return TaskLifecycleCommandOutcome(
            exit_code=1,
            error_message=_LIFECYCLE_CONFIRMATION_REQUIRED,
        )
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(task_id) is not int
        or task_id <= 0
        or type(target_status) is not TaskLifecycleStatus
    ):
        return TaskLifecycleCommandOutcome(
            exit_code=1,
            error_message=_LIFECYCLE_INVALID_DATA,
        )

    try:
        session = (session_factory or SessionLocal)()
    except Exception:
        return TaskLifecycleCommandOutcome(
            exit_code=1,
            error_message=_LIFECYCLE_FAILED,
        )

    committed = False
    outcome: TaskLifecycleCommandOutcome
    try:
        try:
            repository = (task_repository_factory or TaskRepository)(session)
            service = (service_factory or TaskLifecycleService)(repository)
            result = service.transition(company_id, task_id, target_status)
            session.commit()
            committed = True
            outcome = TaskLifecycleCommandOutcome(exit_code=0, result=result)
        except (
            TaskLifecycleInvalidDataError,
            TaskLifecycleNotFoundError,
            TaskLifecycleTransitionError,
            TaskLifecycleConsistencyError,
        ) as error:
            try:
                session.rollback()
            except Exception:
                outcome = TaskLifecycleCommandOutcome(
                    exit_code=1,
                    error_message=_LIFECYCLE_FAILED,
                )
            else:
                outcome = TaskLifecycleCommandOutcome(
                    exit_code=1,
                    error_message=_task_lifecycle_error_message(error),
                )
        except Exception:
            if not committed:
                with suppress(Exception):
                    session.rollback()
            outcome = TaskLifecycleCommandOutcome(
                exit_code=1,
                error_message=_LIFECYCLE_FAILED,
            )
    except BaseException:
        with suppress(BaseException):
            session.close()
        raise

    try:
        session.close()
    except Exception:
        return TaskLifecycleCommandOutcome(
            exit_code=1,
            error_message=_LIFECYCLE_FAILED,
        )
    return outcome


def _task_lifecycle_error_message(
    error: TaskLifecycleInvalidDataError
    | TaskLifecycleNotFoundError
    | TaskLifecycleTransitionError
    | TaskLifecycleConsistencyError,
) -> str:
    if isinstance(error, TaskLifecycleInvalidDataError):
        return _LIFECYCLE_INVALID_DATA
    if isinstance(error, TaskLifecycleNotFoundError):
        return _LIFECYCLE_NOT_FOUND
    if isinstance(error, TaskLifecycleTransitionError):
        return _LIFECYCLE_TRANSITION_NOT_ALLOWED
    return _LIFECYCLE_INCONSISTENT_STATE


def _print_task_lifecycle_result(result: TaskLifecycleResult) -> None:
    typer.echo(f"Task ID: {result.task_id}")
    typer.echo(f"Company ID: {result.company_id}")
    typer.echo(f"Previous Status: {result.previous_status.value}")
    typer.echo(f"Current Status: {result.current_status.value}")
    typer.echo(f"Changed: {'true' if result.changed else 'false'}")


def _run_task_lifecycle_command(
    *,
    company_id: int,
    task_id: int,
    target_status: TaskLifecycleStatus,
    yes: bool,
) -> None:
    outcome = execute_task_lifecycle_transition(
        company_id=company_id,
        task_id=task_id,
        target_status=target_status,
        yes=yes,
    )
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    if outcome.result is not None:
        _print_task_lifecycle_result(outcome.result)
    raise typer.Exit(outcome.exit_code)


@app.command("start")
def start_task(
    company_id: int = typer.Option(help="Company ID."),
    task_id: int = typer.Option(help="Task ID."),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm Task lifecycle transition.",
    ),
) -> None:
    _run_task_lifecycle_command(
        company_id=company_id,
        task_id=task_id,
        target_status=TaskLifecycleStatus.IN_PROGRESS,
        yes=yes,
    )


@app.command("complete")
def complete_task(
    company_id: int = typer.Option(help="Company ID."),
    task_id: int = typer.Option(help="Task ID."),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm Task lifecycle transition.",
    ),
) -> None:
    _run_task_lifecycle_command(
        company_id=company_id,
        task_id=task_id,
        target_status=TaskLifecycleStatus.DONE,
        yes=yes,
    )


@app.command("cancel")
def cancel_task(
    company_id: int = typer.Option(help="Company ID."),
    task_id: int = typer.Option(help="Task ID."),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm Task lifecycle transition.",
    ),
) -> None:
    _run_task_lifecycle_command(
        company_id=company_id,
        task_id=task_id,
        target_status=TaskLifecycleStatus.CANCELLED,
        yes=yes,
    )


def execute_create_task_for_lead(
    *,
    company_id: int,
    lead_id: int,
    title: str,
    yes: bool,
    description: str | None = None,
    session_factory: SessionFactory | None = None,
    lead_repository_factory: LeadRepositoryFactory | None = None,
    task_repository_factory: TaskRepositoryFactory | None = None,
    service_factory: LeadTaskCreationServiceFactory | None = None,
) -> LeadTaskCreationCommandOutcome:
    if yes is not True:
        return LeadTaskCreationCommandOutcome(
            exit_code=1,
            error_message=_CONFIRMATION_REQUIRED,
        )
    if (
        not _valid_lead_task_id(company_id)
        or not _valid_lead_task_id(lead_id)
        or not _valid_lead_task_title(title)
        or not _valid_lead_task_description(description)
    ):
        return LeadTaskCreationCommandOutcome(
            exit_code=1,
            error_message=_INVALID_DATA,
        )

    try:
        session = (session_factory or SessionLocal)()
    except Exception:
        return LeadTaskCreationCommandOutcome(
            exit_code=1,
            error_message=_CREATION_FAILED,
        )

    committed = False
    rollback_attempted = False
    outcome: LeadTaskCreationCommandOutcome
    try:
        try:
            try:
                lead_repository = (lead_repository_factory or LeadRepository)(session)
                task_repository = (task_repository_factory or TaskRepository)(session)
                service = (service_factory or LeadTaskCreationService)(
                    lead_repository,
                    task_repository,
                )
                result = service.create(company_id, lead_id, title, description)
                session.commit()
                committed = True
                outcome = LeadTaskCreationCommandOutcome(exit_code=0, result=result)
            except (
                LeadTaskCreationInvalidDataError,
                LeadTaskCreationNotFoundError,
                LeadTaskCreationConsistencyError,
            ) as error:
                rollback_attempted = True
                if _rollback_lead_task_session(session):
                    outcome = LeadTaskCreationCommandOutcome(
                        exit_code=1,
                        error_message=_lead_task_creation_error_message(error),
                    )
                else:
                    outcome = LeadTaskCreationCommandOutcome(
                        exit_code=1,
                        error_message=_CREATION_FAILED,
                    )
            except Exception:
                if not committed and not rollback_attempted:
                    rollback_attempted = True
                    _rollback_lead_task_session(session)
                outcome = LeadTaskCreationCommandOutcome(
                    exit_code=1,
                    error_message=_CREATION_FAILED,
                )
        except BaseException:
            with suppress(BaseException):
                session.close()
            raise
        else:
            session.close()
    except Exception:
        return LeadTaskCreationCommandOutcome(
            exit_code=1,
            error_message=_CREATION_FAILED,
        )
    return outcome


def _valid_lead_task_id(value: object) -> bool:
    return type(value) is int and value > 0


def _valid_lead_task_title(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and len(value) <= 255


def _valid_lead_task_description(value: object) -> bool:
    return value is None or type(value) is str


def _rollback_lead_task_session(session: Session) -> bool:
    try:
        session.rollback()
    except Exception:
        return False
    return True


def _lead_task_creation_error_message(
    error: LeadTaskCreationInvalidDataError
    | LeadTaskCreationNotFoundError
    | LeadTaskCreationConsistencyError,
) -> str:
    if isinstance(error, LeadTaskCreationInvalidDataError):
        return _INVALID_DATA
    if isinstance(error, LeadTaskCreationNotFoundError):
        return _NOT_FOUND
    return _INCONSISTENT_STATE


def _print_lead_task_creation_result(result: LeadTaskCreationResult) -> None:
    typer.echo(f"Task ID: {result.task_id}")
    typer.echo(f"Company ID: {result.company_id}")
    typer.echo(f"Lead ID: {result.lead_id}")
    typer.echo(f"Status: {result.status}")


@app.command("create-for-lead")
def create_task_for_lead(
    company_id: int = typer.Option(help="Company ID."),
    lead_id: int = typer.Option(help="Lead ID."),
    title: str = typer.Option(help="Task title."),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Task description.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm Task creation.",
    ),
) -> None:
    outcome = execute_create_task_for_lead(
        company_id=company_id,
        lead_id=lead_id,
        title=title,
        description=description,
        yes=yes,
    )
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    if outcome.result is not None:
        _print_lead_task_creation_result(outcome.result)
    raise typer.Exit(outcome.exit_code)


@app.command("create")
def create_task(
    lead_id: int,
    title: str,
    description: str = "",
    status: str = "TODO",
    due_at: str = "",
) -> None:
    parsed_due_at: datetime | None = None

    if due_at:
        try:
            parsed_due_at = datetime.fromisoformat(due_at)
        except ValueError as error:
            typer.secho(
                "Invalid due_at. Use an ISO datetime, for example 2026-07-08T14:30:00.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1) from error

    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        task = service.create(
            TaskCreate(
                lead_id=lead_id,
                title=title,
                description=description or None,
                status=status,
                due_at=parsed_due_at,
            )
        )

    typer.secho("Task created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {task.id}")
    typer.echo(f"Lead ID: {task.lead_id}")
    typer.echo(f"Title: {task.title}")
    typer.echo(f"Status: {task.status}")
    typer.echo(f"Due at: {task.due_at}")


@app.command("list")
def list_tasks() -> None:
    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        tasks = service.get_all()

    if not tasks:
        typer.echo("No tasks found.")
        return

    typer.echo("\nTasks\n")

    for task in tasks:
        due_at = str(task.due_at) if task.due_at is not None else "-"
        typer.echo(f"{task.id:>3}  {task.lead_id:>3}  {task.title}  {task.status}  {due_at}")


@app.command("show")
def show_task(task_id: int) -> None:
    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        task = service.get(task_id)

    if task is None:
        typer.secho("Task not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo()
    typer.echo(f"ID:          {task.id}")
    typer.echo(f"Lead ID:     {task.lead_id}")
    typer.echo(f"Title:       {task.title}")
    typer.echo(f"Description: {task.description}")
    typer.echo(f"Status:      {task.status}")
    typer.echo(f"Due at:      {task.due_at}")


@app.command("delete")
def delete_task(task_id: int) -> None:
    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        task = repository.get(task_id)

        if task is None:
            typer.secho("Task not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        service.delete(task)

    typer.secho("Task deleted", fg=typer.colors.GREEN)

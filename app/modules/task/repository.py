from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.lead.models import Lead
from app.modules.task.models import Task, TaskLifecycleStatus
from app.modules.task.work_queue import TaskWorkQueueRecord

_INVALID_LIFECYCLE_DATA = "Task lifecycle data is invalid."
_TASK_NOT_FOUND = "Task was not found."
_TRANSITION_NOT_ALLOWED = "Task status transition is not allowed."

_ALLOWED_TRANSITIONS: dict[
    TaskLifecycleStatus,
    frozenset[TaskLifecycleStatus],
] = {
    TaskLifecycleStatus.TODO: frozenset(
        {
            TaskLifecycleStatus.TODO,
            TaskLifecycleStatus.IN_PROGRESS,
            TaskLifecycleStatus.CANCELLED,
        }
    ),
    TaskLifecycleStatus.IN_PROGRESS: frozenset(
        {
            TaskLifecycleStatus.IN_PROGRESS,
            TaskLifecycleStatus.DONE,
            TaskLifecycleStatus.CANCELLED,
        }
    ),
    TaskLifecycleStatus.DONE: frozenset({TaskLifecycleStatus.DONE}),
    TaskLifecycleStatus.CANCELLED: frozenset({TaskLifecycleStatus.CANCELLED}),
}


class TaskLifecycleRepositoryNotFoundError(ValueError):
    pass


class TaskLifecycleRepositoryTransitionError(ValueError):
    pass


def _normalize_persisted_task_status(value: object) -> TaskLifecycleStatus:
    if isinstance(value, TaskLifecycleStatus):
        return value
    if type(value) is str:
        try:
            return TaskLifecycleStatus(value)
        except ValueError:
            pass
    raise TaskLifecycleRepositoryTransitionError(_TRANSITION_NOT_ALLOWED)


@dataclass(frozen=True)
class TaskStatusTransitionResult:
    task: Task
    previous_status: TaskLifecycleStatus
    current_status: TaskLifecycleStatus
    changed: bool


class TaskRepository:
    """
    Repository for Task entity.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        lead_id: int,
        title: str,
        description: str | None = None,
        status: str | None = None,
        due_at: datetime | None = None,
    ) -> Task:
        task = Task(
            lead_id=lead_id,
            title=title,
            description=description,
            due_at=due_at,
        )

        if status is not None:
            task.status = status

        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

        return task

    def create_for_lead(
        self,
        *,
        lead_id: int,
        title: str,
        description: str | None = None,
    ) -> Task:
        if (
            type(lead_id) is not int
            or lead_id <= 0
            or type(title) is not str
            or not title.strip()
            or len(title) > 255
            or (description is not None and type(description) is not str)
        ):
            raise ValueError("Task creation data is invalid.")

        task = Task(
            lead_id=lead_id,
            title=title,
            description=description,
            status="TODO",
            due_at=None,
        )
        self.session.add(task)
        self.session.flush()

        return task

    def set_status_for_company(
        self,
        company_id: int,
        task_id: int,
        target_status: TaskLifecycleStatus,
    ) -> TaskStatusTransitionResult:
        if (
            type(company_id) is not int
            or company_id <= 0
            or type(task_id) is not int
            or task_id <= 0
            or type(target_status) is not TaskLifecycleStatus
        ):
            raise ValueError(_INVALID_LIFECYCLE_DATA)

        statement = (
            select(Task)
            .join(Lead, Task.lead_id == Lead.id)
            .where(
                Lead.company_id == company_id,
                Task.id == task_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        task = self.session.scalar(statement)
        if task is None:
            raise TaskLifecycleRepositoryNotFoundError(_TASK_NOT_FOUND)

        previous_status = _normalize_persisted_task_status(task.status)
        if target_status not in _ALLOWED_TRANSITIONS[previous_status]:
            raise TaskLifecycleRepositoryTransitionError(_TRANSITION_NOT_ALLOWED)

        changed = previous_status is not target_status
        if changed:
            task.status = target_status.value
            self.session.add(task)
            self.session.flush()

        return TaskStatusTransitionResult(
            task=task,
            previous_status=previous_status,
            current_status=target_status,
            changed=changed,
        )

    def list_work_queue_for_company(
        self,
        company_id: int,
        as_of: datetime,
        upcoming_until: datetime,
    ) -> list[TaskWorkQueueRecord]:
        if (
            type(company_id) is not int
            or company_id <= 0
            or type(as_of) is not datetime
            or as_of.tzinfo is not None
            or type(upcoming_until) is not datetime
            or upcoming_until.tzinfo is not None
            or upcoming_until <= as_of
        ):
            raise ValueError("Task work queue data is invalid.")

        statement = (
            select(Task.id, Task.lead_id, Task.title, Task.status, Task.due_at)
            .join(Lead, Task.lead_id == Lead.id)
            .where(
                Lead.company_id == company_id,
                Task.status.in_(
                    (
                        TaskLifecycleStatus.TODO.value,
                        TaskLifecycleStatus.IN_PROGRESS.value,
                    )
                ),
                or_(Task.due_at.is_(None), Task.due_at <= upcoming_until),
            )
            .order_by(Task.id)
        )
        rows = self.session.execute(statement).all()
        return [
            TaskWorkQueueRecord(
                task_id=row.id,
                lead_id=row.lead_id,
                title=row.title,
                status=row.status,
                due_at=row.due_at,
            )
            for row in rows
        ]

    def get(self, task_id: int) -> Task | None:
        statement = select(Task).where(Task.id == task_id)
        return self.session.scalar(statement)

    def get_all(self) -> list[Task]:
        statement = select(Task).order_by(Task.id)
        return list(self.session.scalars(statement))

    def get_by_lead(self, lead_id: int) -> list[Task]:
        statement = select(Task).where(Task.lead_id == lead_id).order_by(Task.id)

        return list(self.session.scalars(statement))

    def update(self, task: Task) -> Task:
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

        return task

    def delete(self, task: Task) -> None:
        self.session.delete(task)
        self.session.commit()

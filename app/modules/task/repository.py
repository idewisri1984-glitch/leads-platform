from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.task.models import Task


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

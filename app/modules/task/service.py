from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository
from app.modules.task.schemas import TaskCreate, TaskRead


class TaskService:
    """
    Task business logic.
    """

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def create(self, data: TaskCreate) -> TaskRead:
        task = self.repository.create(
            lead_id=data.lead_id,
            title=data.title,
            description=data.description,
            status=data.status,
            due_at=data.due_at,
        )

        return TaskRead.model_validate(task)

    def get(self, task_id: int) -> TaskRead | None:
        task = self.repository.get(task_id)

        if task is None:
            return None

        return TaskRead.model_validate(task)

    def get_all(self) -> list[TaskRead]:
        tasks = self.repository.get_all()

        return [TaskRead.model_validate(task) for task in tasks]

    def get_by_lead(self, lead_id: int) -> list[TaskRead]:
        tasks = self.repository.get_by_lead(lead_id)

        return [TaskRead.model_validate(task) for task in tasks]

    def update(self, task: Task) -> TaskRead:
        task = self.repository.update(task)

        return TaskRead.model_validate(task)

    def delete(self, task: Task) -> None:
        self.repository.delete(task)

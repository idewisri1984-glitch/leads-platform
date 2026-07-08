from datetime import datetime

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository
from app.modules.task.repository import TaskRepository
from app.modules.task.schemas import TaskCreate, TaskRead
from app.modules.task.service import TaskService


def create_lead(name: str = "Task Service") -> int:
    with SessionLocal() as session:
        project = ProjectRepository(session).create(f"{name} Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name=f"{name} Company",
        )
        lead = LeadRepository(session).create(company_id=company.id)

        return lead.id


def test_create_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        service = TaskService(TaskRepository(session))
        task = service.create(TaskCreate(lead_id=lead_id, title="Follow up"))

        assert isinstance(task, TaskRead)
        assert task.lead_id == lead_id
        assert task.title == "Follow up"
        assert task.description is None
        assert task.status == "TODO"
        assert task.due_at is None


def test_create_task_with_due_at() -> None:
    lead_id = create_lead()
    due_at = datetime(2026, 11, 10, 14, 30)

    with SessionLocal() as session:
        service = TaskService(TaskRepository(session))
        task = service.create(
            TaskCreate(
                lead_id=lead_id,
                title="Scheduled follow-up",
                due_at=due_at,
            )
        )

        assert task.due_at == due_at


def test_get_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        service = TaskService(TaskRepository(session))
        created = service.create(TaskCreate(lead_id=lead_id, title="Get task"))

        loaded = service.get(created.id)

        assert loaded is not None
        assert loaded.id == created.id


def test_get_all_tasks() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        service = TaskService(TaskRepository(session))
        first = service.create(TaskCreate(lead_id=lead_id, title="First task"))
        second = service.create(TaskCreate(lead_id=lead_id, title="Second task"))

        tasks = service.get_all()

        assert [task.id for task in tasks] == [first.id, second.id]


def test_get_tasks_by_lead() -> None:
    lead_id = create_lead("First Lead")
    other_lead_id = create_lead("Other Lead")

    with SessionLocal() as session:
        service = TaskService(TaskRepository(session))
        expected = service.create(TaskCreate(lead_id=lead_id, title="Expected task"))
        service.create(TaskCreate(lead_id=other_lead_id, title="Other task"))

        tasks = service.get_by_lead(lead_id)

        assert [task.id for task in tasks] == [expected.id]


def test_update_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        created = service.create(TaskCreate(lead_id=lead_id, title="Original task"))
        task = repository.get(created.id)

        assert task is not None

        task.title = "Updated task"
        task.status = "DONE"
        updated = service.update(task)

        assert updated.title == "Updated task"
        assert updated.status == "DONE"


def test_delete_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        created = service.create(TaskCreate(lead_id=lead_id, title="Delete task"))
        task = repository.get(created.id)

        assert task is not None

        service.delete(task)

        assert service.get(created.id) is None

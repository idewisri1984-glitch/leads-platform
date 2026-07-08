from datetime import datetime

from sqlalchemy import delete

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository
from app.modules.task.repository import TaskRepository


def create_lead(name: str = "Task Repository") -> int:
    with SessionLocal() as session:
        project = ProjectRepository(session).create(f"{name} Project")
        company = CompanyRepository(session).create(
            project_id=project.id,
            name=f"{name} Company",
        )
        lead = LeadRepository(session).create(company_id=company.id)

        return lead.id


def test_create_task_with_required_fields_and_default_status() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        task = TaskRepository(session).create(
            lead_id=lead_id,
            title="Follow up",
        )

        assert task.id is not None
        assert task.lead_id == lead_id
        assert task.title == "Follow up"
        assert task.description is None
        assert task.status == "TODO"
        assert task.due_at is None


def test_create_task_with_optional_fields() -> None:
    lead_id = create_lead()
    due_at = datetime(2026, 9, 15, 10, 30)

    with SessionLocal() as session:
        task = TaskRepository(session).create(
            lead_id=lead_id,
            title="Prepare proposal",
            description="Draft the commercial proposal",
            status="IN_PROGRESS",
            due_at=due_at,
        )

        assert task.description == "Draft the commercial proposal"
        assert task.status == "IN_PROGRESS"
        assert task.due_at == due_at


def test_get_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        task = repository.create(lead_id=lead_id, title="Get task")

        loaded = repository.get(task.id)

        assert loaded is not None
        assert loaded.id == task.id


def test_get_all_tasks() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        first = repository.create(lead_id=lead_id, title="First task")
        second = repository.create(lead_id=lead_id, title="Second task")

        tasks = repository.get_all()

        assert [task.id for task in tasks] == [first.id, second.id]


def test_get_tasks_by_lead_excludes_other_leads() -> None:
    lead_id = create_lead("First Lead")
    other_lead_id = create_lead("Other Lead")

    with SessionLocal() as session:
        repository = TaskRepository(session)
        first = repository.create(lead_id=lead_id, title="First lead task")
        second = repository.create(lead_id=lead_id, title="Another first lead task")
        repository.create(lead_id=other_lead_id, title="Other lead task")

        tasks = repository.get_by_lead(lead_id)

        assert [task.id for task in tasks] == [first.id, second.id]


def test_update_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        task = repository.create(lead_id=lead_id, title="Original title")
        task.title = "Updated title"
        task.description = "Updated description"
        task.status = "DONE"
        task.due_at = datetime(2026, 10, 1, 9, 0)

        updated = repository.update(task)

        assert updated.title == "Updated title"
        assert updated.description == "Updated description"
        assert updated.status == "DONE"
        assert updated.due_at == datetime(2026, 10, 1, 9, 0)


def test_delete_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        task = repository.create(lead_id=lead_id, title="Delete task")
        task_id = task.id

        repository.delete(task)

        assert repository.get(task_id) is None


def test_deleting_lead_cascades_to_tasks_at_database_level() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        repository = TaskRepository(session)
        first = repository.create(lead_id=lead_id, title="First cascade task")
        second = repository.create(lead_id=lead_id, title="Second cascade task")
        task_ids = [first.id, second.id]

        session.execute(delete(Lead).where(Lead.id == lead_id))
        session.commit()

    with SessionLocal() as session:
        repository = TaskRepository(session)

        assert [repository.get(task_id) for task_id in task_ids] == [None, None]

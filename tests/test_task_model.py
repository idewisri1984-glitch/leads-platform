from datetime import datetime

from sqlalchemy import delete

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task


def create_lead() -> int:
    with SessionLocal() as session:
        project = Project(name="Task Model Project")
        company = Company(project=project, name="Task Model Company")
        lead = Lead(company=company)
        session.add(project)
        session.commit()

        return lead.id


def test_create_task_with_lead_relationship() -> None:
    lead_id = create_lead()
    due_at = datetime(2026, 8, 1, 12, 30)

    with SessionLocal() as session:
        lead = session.get_one(Lead, lead_id)
        task = Task(
            lead=lead,
            title="Follow up",
            description="Call the lead",
            due_at=due_at,
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        assert task.id is not None
        assert task.lead_id == lead_id
        assert task.title == "Follow up"
        assert task.description == "Call the lead"
        assert task.status == "TODO"
        assert task.due_at == due_at
        assert task in lead.tasks


def test_removing_task_from_lead_deletes_orphan() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        lead = session.get_one(Lead, lead_id)
        task = Task(title="Orphaned task")
        lead.tasks.append(task)
        session.commit()
        task_id = task.id

        lead.tasks.remove(task)
        session.commit()

        assert session.get(Task, task_id) is None


def test_deleting_lead_cascades_to_task() -> None:
    lead_id = create_lead()

    with SessionLocal() as session:
        task = Task(lead_id=lead_id, title="Delete with lead")
        session.add(task)
        session.commit()
        task_id = task.id

        session.execute(delete(Lead).where(Lead.id == lead_id))
        session.commit()

    with SessionLocal() as session:
        assert session.get(Task, task_id) is None

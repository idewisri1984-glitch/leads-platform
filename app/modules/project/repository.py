from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.project.models import Project


class ProjectRepository:
    """
    Repository for Project entity.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, name: str) -> Project:
        project = Project(name=name)

        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)

        return project

    def get(self, project_id: int) -> Project | None:
        statement = select(Project).where(Project.id == project_id)

        return self.session.scalar(statement)

    def get_all(self) -> list[Project]:
        statement = select(Project).order_by(Project.id)

        return list(self.session.scalars(statement))

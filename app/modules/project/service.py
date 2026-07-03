from app.modules.project.repository import ProjectRepository
from app.modules.project.schemas import ProjectCreate, ProjectRead


class ProjectService:
    """
    Project business logic.
    """

    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    def create(self, data: ProjectCreate) -> ProjectRead:
        project = self.repository.create(data.name)

        return ProjectRead.model_validate(project)

    def get_all(self) -> list[ProjectRead]:
        projects = self.repository.get_all()

        return [ProjectRead.model_validate(project) for project in projects]

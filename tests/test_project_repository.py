from app.core.database.session import SessionLocal
from app.modules.project.repository import ProjectRepository


def test_create_project() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)

        project = repository.create("My First Project")

        assert project.id is not None
        assert project.name == "My First Project"
from app.core.database.session import SessionLocal
from app.modules.project import ProjectCreate, ProjectRepository, ProjectService


def test_create_project_service() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)
        service = ProjectService(repository)

        project = service.create(
            ProjectCreate(
                name="Bali Villa",
            )
        )

        assert project.id > 0
        assert project.name == "Bali Villa"

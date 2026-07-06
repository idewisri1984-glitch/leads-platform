from app.core.database.session import SessionLocal
from app.modules.project.repository import ProjectRepository


def test_create_project() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)

        project = repository.create("My First Project")

        assert project.id is not None
        assert project.name == "My First Project"


def test_get_project() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)

        project = repository.create("Repository Test")

        loaded = repository.get(project.id)

        assert loaded is not None
        assert loaded.id == project.id
        assert loaded.name == "Repository Test"


def test_get_all_projects() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)

        projects = repository.get_all()

        assert isinstance(projects, list)


def test_update_project() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)

        project = repository.create("Old Name")

        project.name = "New Name"

        updated = repository.update(project)

        assert updated.name == "New Name"


def test_delete_project() -> None:
    with SessionLocal() as session:
        repository = ProjectRepository(session)

        project = repository.create("Delete Me")

        project_id = project.id

        repository.delete(project)

        deleted = repository.get(project_id)

        assert deleted is None

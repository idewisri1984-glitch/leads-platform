from typing import cast

from app.core.database.session import SessionLocal
from app.modules.project import Project, ProjectCreate, ProjectRepository, ProjectService


class StubProjectRepository:
    def __init__(self, project: Project | None) -> None:
        self.project = project
        self.get_calls: list[int] = []
        self.write_calls = 0

    def get(self, project_id: int) -> Project | None:
        self.get_calls.append(project_id)
        return self.project

    def create(self, name: str) -> Project:
        self.write_calls += 1
        raise AssertionError("ProjectService.get must not create a project.")

    def update(self, project: Project) -> Project:
        self.write_calls += 1
        raise AssertionError("ProjectService.get must not update a project.")

    def delete(self, project: Project) -> None:
        self.write_calls += 1
        raise AssertionError("ProjectService.get must not delete a project.")


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


def test_get_existing_project_delegates_without_writes() -> None:
    repository = StubProjectRepository(Project(id=7, name="Existing project"))
    service = ProjectService(cast(ProjectRepository, repository))

    project = service.get(7)

    assert project is not None
    assert project.id == 7
    assert project.name == "Existing project"
    assert repository.get_calls == [7]
    assert repository.write_calls == 0


def test_get_missing_project_returns_none_without_writes() -> None:
    repository = StubProjectRepository(None)
    service = ProjectService(cast(ProjectRepository, repository))

    project = service.get(404)

    assert project is None
    assert repository.get_calls == [404]
    assert repository.write_calls == 0

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.project.repository import ProjectRepository


def test_create_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="OpenAI",
        )

        assert company.id is not None
        assert company.name == "OpenAI"
        assert company.project_id == project.id


def test_get_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="Microsoft",
        )

        loaded = company_repository.get(company.id)

        assert loaded is not None
        assert loaded.id == company.id
        assert loaded.name == "Microsoft"


def test_get_all_companies() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company_repository.create(
            project_id=project.id,
            name="Google",
        )

        companies = company_repository.get_all()

        assert isinstance(companies, list)
        assert len(companies) >= 1


def test_get_companies_by_project() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company_repository.create(
            project_id=project.id,
            name="Apple",
        )

        companies = company_repository.get_by_project(project.id)

        assert len(companies) >= 1
        assert companies[0].project_id == project.id


def test_update_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="Old Name",
        )

        company.name = "New Name"

        updated = company_repository.update(company)

        assert updated.name == "New Name"


def test_delete_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="Delete Me",
        )

        company_id = company.id

        company_repository.delete(company)

        deleted = company_repository.get(company_id)

        assert deleted is None

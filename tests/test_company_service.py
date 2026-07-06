from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.company.schemas import CompanyCreate
from app.modules.company.service import CompanyService
from app.modules.project.repository import ProjectRepository


def test_create_company_service() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)
        company_service = CompanyService(company_repository)

        project = project_repository.create("Service Test")

        company = company_service.create(
            CompanyCreate(
                project_id=project.id,
                name="OpenAI",
            )
        )

        assert company.id is not None
        assert company.project_id == project.id
        assert company.name == "OpenAI"


def test_get_all_company_service() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)
        company_service = CompanyService(company_repository)

        project = project_repository.create("Service Test")

        company_service.create(
            CompanyCreate(
                project_id=project.id,
                name="Google",
            )
        )

        companies = company_service.get_all()

        assert isinstance(companies, list)
        assert len(companies) >= 1

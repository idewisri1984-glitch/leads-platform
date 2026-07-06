from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.company.schemas import CompanyCreate, CompanyRead


class CompanyService:
    """
    Company business logic.
    """

    def __init__(self, repository: CompanyRepository) -> None:
        self.repository = repository

    def create(self, data: CompanyCreate) -> CompanyRead:
        company = self.repository.create(
            project_id=data.project_id,
            name=data.name,
            website=data.website,
            country=data.country,
            city=data.city,
            industry=data.industry,
            status=data.status,
            notes=data.notes,
        )

        return CompanyRead.model_validate(company)

    def get(self, company_id: int) -> CompanyRead | None:
        company = self.repository.get(company_id)

        if company is None:
            return None

        return CompanyRead.model_validate(company)

    def get_all(self) -> list[CompanyRead]:
        companies = self.repository.get_all()

        return [CompanyRead.model_validate(company) for company in companies]

    def get_by_project(self, project_id: int) -> list[CompanyRead]:
        companies = self.repository.get_by_project(project_id)

        return [CompanyRead.model_validate(company) for company in companies]

    def update(self, company: Company) -> CompanyRead:
        company = self.repository.update(company)

        return CompanyRead.model_validate(company)

    def delete(self, company: Company) -> None:
        self.repository.delete(company)

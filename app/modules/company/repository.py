from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_import.normalization import normalize_website_hostname


class CompanyRepository:
    """
    Repository for Company entity.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        project_id: int,
        name: str,
        website: str | None = None,
        country: str | None = None,
        city: str | None = None,
        industry: str | None = None,
        status: str | None = None,
        notes: str | None = None,
    ) -> Company:
        company = Company(
            project_id=project_id,
            name=name,
            website=website,
            country=country,
            city=city,
            industry=industry,
            status=status,
            notes=notes,
        )

        self.session.add(company)
        self.session.commit()
        self.session.refresh(company)

        return company

    def get(self, company_id: int) -> Company | None:
        statement = select(Company).where(Company.id == company_id)
        return self.session.scalar(statement)

    def get_all(self) -> list[Company]:
        statement = select(Company).order_by(Company.id)
        return list(self.session.scalars(statement))

    def get_by_project(self, project_id: int) -> list[Company]:
        statement = select(Company).where(Company.project_id == project_id).order_by(Company.id)

        return list(self.session.scalars(statement))

    def get_for_project(self, project_id: int, company_id: int) -> Company | None:
        self._validate_positive_id(project_id, "Project")
        self._validate_positive_id(company_id, "Company")
        statement = select(Company).where(
            Company.id == company_id,
            Company.project_id == project_id,
        )
        return self.session.scalar(statement)

    def find_duplicate_by_website(self, project_id: int, website: str) -> Company | None:
        self._validate_positive_id(project_id, "Project")
        if not isinstance(website, str):
            raise ValueError("Website must be a string.")

        hostname = normalize_website_hostname(website)
        if hostname is None:
            return None

        statement = select(Company).where(Company.project_id == project_id).order_by(Company.id)
        for company in self.session.scalars(statement):
            try:
                existing_hostname = normalize_website_hostname(company.website)
            except ValueError:
                continue
            if existing_hostname == hostname:
                return company
        return None

    def create_for_promotion(
        self,
        *,
        project_id: int,
        name: str,
        website: str | None,
        country: str | None,
        status: str = "NEW",
    ) -> Company:
        self._validate_positive_id(project_id, "Project")
        company = Company(
            project_id=project_id,
            name=name,
            website=website,
            country=country,
            status=status,
        )
        self.session.add(company)
        self.session.flush()
        return company

    def update(self, company: Company) -> Company:
        self.session.add(company)
        self.session.commit()
        self.session.refresh(company)

        return company

    def delete(self, company: Company) -> None:
        self.session.delete(company)
        self.session.commit()

    @staticmethod
    def _validate_positive_id(value: int, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} ID must be a positive integer.")

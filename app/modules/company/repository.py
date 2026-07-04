from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.company.models import Company


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

    def update(self, company: Company) -> Company:
        self.session.add(company)
        self.session.commit()
        self.session.refresh(company)

        return company

    def delete(self, company: Company) -> None:
        self.session.delete(company)
        self.session.commit()

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment


class CompanyEnrichmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_company_id(self, company_id: int) -> CompanyEnrichment | None:
        return self.session.scalar(
            select(CompanyEnrichment).where(CompanyEnrichment.company_id == company_id)
        )

    def create(self, *, company_id: int, **values: Any) -> CompanyEnrichment:
        enrichment = CompanyEnrichment(company_id=company_id, **values)
        self.session.add(enrichment)
        self.session.flush()
        return enrichment

    def update(self, enrichment: CompanyEnrichment, **values: Any) -> CompanyEnrichment:
        for field, value in values.items():
            setattr(enrichment, field, value)
        self.session.add(enrichment)
        self.session.flush()
        return enrichment

    def get_or_create_for_company(self, company_id: int) -> tuple[CompanyEnrichment, bool]:
        existing = self.get_by_company_id(company_id)
        if existing is not None:
            return existing, False
        return self.create(company_id=company_id), True

    def list_for_project(self, project_id: int, limit: int) -> list[CompanyEnrichment]:
        statement = (
            select(CompanyEnrichment)
            .join(Company)
            .where(Company.project_id == project_id)
            .order_by(Company.id)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def list_companies_for_project(self, project_id: int, limit: int) -> list[Company]:
        statement = (
            select(Company)
            .where(Company.project_id == project_id)
            .order_by(Company.id)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def delete(self, enrichment_id: int) -> None:
        enrichment = self.session.get(CompanyEnrichment, enrichment_id)
        if enrichment is not None:
            self.session.delete(enrichment)
            self.session.flush()

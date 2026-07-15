from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.company_enrichment.schemas import CompanyEnrichmentSelectionOptions


@dataclass(frozen=True)
class CompanyEnrichmentSelectionResult:
    companies: list[Company]
    matched_count: int
    selected_count: int
    skipped_by_filters_count: int


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

    def select_companies_for_enrichment(
        self,
        project_id: int,
        limit: int,
        *,
        options: CompanyEnrichmentSelectionOptions,
        checked_before: datetime | None = None,
    ) -> CompanyEnrichmentSelectionResult:
        filters = [Company.project_id == project_id]
        if options.company_id is not None:
            filters.append(Company.id == options.company_id)
        if options.only_missing:
            filters.append(
                or_(
                    CompanyEnrichment.id.is_(None),
                    CompanyEnrichment.email.is_(None),
                    CompanyEnrichment.phone.is_(None),
                    CompanyEnrichment.instagram_url.is_(None),
                    CompanyEnrichment.linkedin_url.is_(None),
                    CompanyEnrichment.contact_page_url.is_(None),
                    CompanyEnrichment.about_page_url.is_(None),
                    CompanyEnrichment.source_url.is_(None),
                )
            )
        if checked_before is not None:
            filters.append(
                or_(
                    CompanyEnrichment.id.is_(None),
                    CompanyEnrichment.website_checked_at.is_(None),
                    CompanyEnrichment.website_checked_at < checked_before,
                )
            )
        if options.status is not None:
            filters.append(CompanyEnrichment.enrichment_status == options.status)

        total_project_companies = (
            self.session.scalar(
                select(func.count()).select_from(Company).where(Company.project_id == project_id)
            )
            or 0
        )
        matched_count = (
            self.session.scalar(
                select(func.count())
                .select_from(Company)
                .outerjoin(CompanyEnrichment)
                .where(*filters)
            )
            or 0
        )
        statement = (
            select(Company)
            .outerjoin(CompanyEnrichment)
            .where(*filters)
            .order_by(Company.id)
            .limit(limit)
        )
        companies = list(self.session.scalars(statement))
        return CompanyEnrichmentSelectionResult(
            companies=companies,
            matched_count=matched_count,
            selected_count=len(companies),
            skipped_by_filters_count=total_project_companies - matched_count,
        )

    def delete(self, enrichment_id: int) -> None:
        enrichment = self.session.get(CompanyEnrichment, enrichment_id)
        if enrichment is not None:
            self.session.delete(enrichment)
            self.session.flush()

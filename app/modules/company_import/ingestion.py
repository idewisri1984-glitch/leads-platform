from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.company_import.normalization import (
    normalize_text_identity,
    normalize_website_hostname,
)
from app.modules.company_import.schemas import (
    CompanyIngestionDuplicate,
    CompanyIngestionError,
    CompanyIngestionItem,
    CompanyIngestionResult,
)
from app.modules.project.models import Project

type FallbackIdentity = tuple[str, str, str]


def _fallback_identity(
    name: str | None,
    country: str | None,
    city: str | None,
) -> FallbackIdentity | None:
    normalized_name = normalize_text_identity(name)
    normalized_country = normalize_text_identity(country)
    normalized_city = normalize_text_identity(city)

    if normalized_name is None or normalized_country is None or normalized_city is None:
        return None

    return normalized_name, normalized_country, normalized_city


def _fallback_display(identity: FallbackIdentity) -> str:
    return " | ".join(identity)


class CompanyIngestionService:
    """
    Persist source-independent company data with project-scoped deduplication.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest(
        self,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> CompanyIngestionResult:
        total_rows = len(items)
        duplicates: list[CompanyIngestionDuplicate] = []
        errors: list[CompanyIngestionError] = []

        try:
            project = self.session.get(Project, project_id)
        except SQLAlchemyError:
            return self._persistence_failure(total_rows, duplicates, errors)

        if project is None:
            self.session.rollback()
            return CompanyIngestionResult(
                total_rows=total_rows,
                imported=0,
                skipped_duplicates=0,
                failed=total_rows,
                created_company_ids=[],
                duplicates=[],
                errors=[
                    CompanyIngestionError(
                        source_row_number=None,
                        code="project_not_found",
                        message=f"Project {project_id} not found.",
                    )
                ],
                rolled_back=True,
            )

        statement = select(Company).where(Company.project_id == project_id).order_by(Company.id)

        try:
            existing_companies = list(self.session.scalars(statement))
        except SQLAlchemyError:
            return self._persistence_failure(total_rows, duplicates, errors)

        website_index: dict[str, Company] = {}
        fallback_index: dict[FallbackIdentity, Company] = {}

        for company in existing_companies:
            try:
                hostname = normalize_website_hostname(company.website)
            except ValueError:
                hostname = None

            if hostname is not None:
                website_index.setdefault(hostname, company)

            fallback = _fallback_identity(company.name, company.country, company.city)

            if fallback is not None:
                fallback_index.setdefault(fallback, company)

        created_company_ids: list[int] = []

        try:
            for item in items:
                try:
                    hostname = normalize_website_hostname(item.website)
                except ValueError as error:
                    errors.append(
                        CompanyIngestionError(
                            source_row_number=item.source_row_number,
                            code="invalid_website",
                            message=str(error),
                        )
                    )
                    continue

                fallback = _fallback_identity(item.name, item.country, item.city)
                duplicate: Company | None = None
                matched_by: str | None = None
                matched_value: str | None = None

                if hostname is not None and hostname in website_index:
                    duplicate = website_index[hostname]
                    matched_by = "website_hostname"
                    matched_value = hostname
                elif hostname is None and fallback is not None and fallback in fallback_index:
                    duplicate = fallback_index[fallback]
                    matched_by = "name_country_city"
                    matched_value = _fallback_display(fallback)

                if duplicate is not None and matched_by is not None and matched_value is not None:
                    duplicates.append(
                        CompanyIngestionDuplicate(
                            source_row_number=item.source_row_number,
                            existing_company_id=duplicate.id,
                            matched_by=matched_by,
                            matched_value=matched_value,
                        )
                    )
                    continue

                company = Company(
                    project_id=project_id,
                    name=item.name,
                    website=item.website,
                    country=item.country,
                    city=item.city,
                    industry=item.industry,
                    status=item.status,
                    notes=item.notes,
                )
                self.session.add(company)
                self.session.flush()
                created_company_ids.append(company.id)

                if hostname is not None:
                    website_index.setdefault(hostname, company)

                if fallback is not None:
                    fallback_index.setdefault(fallback, company)

            self.session.commit()
        except SQLAlchemyError:
            return self._persistence_failure(total_rows, duplicates, errors)

        return CompanyIngestionResult(
            total_rows=total_rows,
            imported=len(created_company_ids),
            skipped_duplicates=len(duplicates),
            failed=len(errors),
            created_company_ids=created_company_ids,
            duplicates=duplicates,
            errors=errors,
            rolled_back=False,
        )

    def _persistence_failure(
        self,
        total_rows: int,
        duplicates: list[CompanyIngestionDuplicate],
        errors: list[CompanyIngestionError],
    ) -> CompanyIngestionResult:
        self.session.rollback()
        return CompanyIngestionResult(
            total_rows=total_rows,
            imported=0,
            skipped_duplicates=len(duplicates),
            failed=total_rows - len(duplicates),
            created_company_ids=[],
            duplicates=duplicates,
            errors=[
                *errors,
                CompanyIngestionError(
                    source_row_number=None,
                    code="persistence_error",
                    message="Company ingestion was rolled back due to a persistence error.",
                ),
            ],
            rolled_back=True,
        )

from collections.abc import Callable
from datetime import UTC, datetime

from app.modules.company.models import Company
from app.modules.company_enrichment.models import CompanyEnrichment
from app.modules.company_enrichment.normalization import (
    normalize_email,
    normalize_instagram_url,
    normalize_linkedin_company_url,
    normalize_phone,
    normalize_public_url,
)
from app.modules.company_enrichment.provider_interfaces import (
    EnrichmentProvider,
    EnrichmentProviderError,
)
from app.modules.company_enrichment.repository import CompanyEnrichmentRepository
from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentRunItem,
    CompanyEnrichmentRunResult,
    CompanyEnrichmentTarget,
    EnrichmentStatus,
)

_ENRICHMENT_FIELDS = (
    "email",
    "phone",
    "instagram_url",
    "linkedin_url",
    "contact_page_url",
    "about_page_url",
    "source_url",
    "notes",
)
_USEFUL_FIELDS = _ENRICHMENT_FIELDS[:-1]
_NORMALIZERS: dict[str, Callable[[str | None], str | None]] = {
    "website": normalize_public_url,
    "email": normalize_email,
    "phone": normalize_phone,
    "instagram_url": normalize_instagram_url,
    "linkedin_url": normalize_linkedin_company_url,
    "contact_page_url": normalize_public_url,
    "about_page_url": normalize_public_url,
    "source_url": normalize_public_url,
}


class CompanyEnrichmentService:
    def __init__(self, repository: CompanyEnrichmentRepository) -> None:
        self.repository = repository

    def enrich_company(
        self,
        company: Company,
        provider: EnrichmentProvider,
        *,
        dry_run: bool,
        overwrite: bool = False,
    ) -> CompanyEnrichmentRunItem:
        target = CompanyEnrichmentTarget(
            company_id=company.id,
            company_name=company.name,
            website=company.website,
            country=company.country,
            city=company.city,
        )
        existing = self.repository.get_by_company_id(company.id)

        try:
            provider_result = provider.enrich(target)
        except EnrichmentProviderError:
            return self._handle_failure(company, existing, provider.provider_name, dry_run)

        values, validation_errors = self._normalized_values(provider_result)
        errors = [*provider_result.errors, *validation_errors]
        useful = any(values.get(field) is not None for field in _USEFUL_FIELDS)
        status: EnrichmentStatus
        if useful and errors:
            status = "PARTIAL"
        elif useful:
            status = "SUCCEEDED"
        else:
            status = "NOT_FOUND"

        changed_fields: list[str] = []
        if (
            values.get("website") is not None
            and (company.website is None or overwrite)
            and company.website != values["website"]
        ):
            changed_fields.append("website")

        for field in _ENRICHMENT_FIELDS:
            candidate = values.get(field)
            current = getattr(existing, field) if existing is not None else None
            if candidate is not None and (current is None or overwrite) and current != candidate:
                changed_fields.append(field)

        created = existing is None
        updated = existing is not None
        if dry_run:
            return CompanyEnrichmentRunItem(
                company_id=company.id,
                provider=provider_result.provider,
                status=status,
                created=created,
                updated=updated,
                changed_fields=changed_fields,
                errors=self._safe_errors(errors),
            )

        enrichment, created = self.repository.get_or_create_for_company(company.id)
        if values.get("website") is not None and (company.website is None or overwrite):
            company.website = values["website"]
            self.repository.session.add(company)

        persisted: dict[str, object] = {
            "enrichment_status": status,
            "website_checked_at": datetime.now(UTC),
            "last_error": "; ".join(self._safe_errors(errors)) or None,
        }
        for field in _ENRICHMENT_FIELDS:
            candidate = values.get(field)
            current = getattr(enrichment, field)
            if candidate is not None and (current is None or overwrite):
                persisted[field] = candidate
        self.repository.update(enrichment, **persisted)
        self.repository.session.commit()
        return CompanyEnrichmentRunItem(
            company_id=company.id,
            provider=provider_result.provider,
            status=status,
            created=created,
            updated=not created,
            changed_fields=changed_fields,
            errors=self._safe_errors(errors),
        )

    def enrich_project_companies(
        self,
        project_id: int,
        provider: EnrichmentProvider,
        *,
        limit: int,
        dry_run: bool,
        overwrite: bool = False,
    ) -> CompanyEnrichmentRunResult:
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")
        companies = self.repository.list_companies_for_project(project_id, limit)
        items = [
            self.enrich_company(company, provider, dry_run=dry_run, overwrite=overwrite)
            for company in companies
        ]
        return CompanyEnrichmentRunResult(
            project_id=project_id,
            provider=provider.provider_name,
            selected=len(items),
            attempted=len(items),
            created=sum(item.created for item in items),
            updated=sum(item.updated for item in items),
            unchanged=sum(item.unchanged for item in items),
            succeeded=sum(item.status == "SUCCEEDED" for item in items),
            partial=sum(item.status == "PARTIAL" for item in items),
            not_found=sum(item.status == "NOT_FOUND" for item in items),
            failed=sum(item.status == "FAILED" for item in items),
            dry_run=dry_run,
            items=items,
        )

    def _handle_failure(
        self,
        company: Company,
        existing: CompanyEnrichment | None,
        provider_name: str,
        dry_run: bool,
    ) -> CompanyEnrichmentRunItem:
        safe_error = "Enrichment provider failed."
        created = existing is None
        if not dry_run:
            enrichment, created = self.repository.get_or_create_for_company(company.id)
            self.repository.update(
                enrichment,
                enrichment_status="FAILED",
                website_checked_at=datetime.now(UTC),
                last_error=safe_error,
            )
            self.repository.session.commit()
        return CompanyEnrichmentRunItem(
            company_id=company.id,
            provider=provider_name,
            status="FAILED",
            created=created,
            updated=not created,
            errors=[safe_error],
        )

    @staticmethod
    def _normalized_values(
        result: CompanyEnrichmentProviderResult,
    ) -> tuple[dict[str, str | None], list[str]]:
        values: dict[str, str | None] = {"notes": result.notes.strip() if result.notes else None}
        errors: list[str] = []
        for field, normalizer in _NORMALIZERS.items():
            try:
                values[field] = normalizer(getattr(result, field))
            except ValueError:
                values[field] = None
                errors.append(f"Invalid {field} candidate.")
        return values, errors

    @staticmethod
    def _safe_errors(errors: list[str]) -> list[str]:
        return ["Provider reported an enrichment error." for _ in errors]

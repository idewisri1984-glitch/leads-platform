from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)


class FakeEnrichmentProvider:
    """Deterministic no-network provider for CLI boundary validation."""

    provider_name = "fake"

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult:
        return CompanyEnrichmentProviderResult(
            provider=self.provider_name,
            website=target.website,
            source_url=target.website,
            notes="Fake enrichment result.",
        )

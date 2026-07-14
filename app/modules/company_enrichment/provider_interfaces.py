from typing import Protocol, runtime_checkable

from app.modules.company_enrichment.schemas import (
    CompanyEnrichmentProviderResult,
    CompanyEnrichmentTarget,
)


class EnrichmentProviderError(Exception):
    """Controlled enrichment-provider failure safe for service handling."""


@runtime_checkable
class EnrichmentProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    def enrich(self, target: CompanyEnrichmentTarget) -> CompanyEnrichmentProviderResult: ...

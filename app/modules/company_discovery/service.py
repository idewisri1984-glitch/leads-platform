from app.modules.company_discovery.schemas import CompanyDiscoveryRequest, CompanyDiscoveryResult
from app.modules.company_discovery.serpapi_adapter import (
    CompanyDiscoveryAdapterError,
    serpapi_result_to_ingestion_item,
)
from app.modules.company_import.schemas import CompanyIngestionError, CompanyIngestionItem
from app.providers.serpapi import SerpApiClient


class CompanyDiscoveryService:
    """
    Dry-run company discovery orchestration.
    """

    def __init__(self, serpapi_client: SerpApiClient) -> None:
        self.serpapi_client = serpapi_client

    def discover_from_serpapi(
        self,
        request: CompanyDiscoveryRequest,
    ) -> CompanyDiscoveryResult:
        provider_response = self.serpapi_client.search_companies(
            query=request.query,
            country=request.country,
            city=request.city,
            industry=request.industry,
            limit=request.limit,
        )

        items: list[CompanyIngestionItem] = []
        errors: list[CompanyIngestionError] = []

        for result in provider_response.results:
            try:
                items.append(serpapi_result_to_ingestion_item(result, request))
            except CompanyDiscoveryAdapterError as error:
                errors.append(
                    CompanyIngestionError(
                        source_row_number=result.position,
                        code="invalid_discovery_result",
                        message=str(error),
                    )
                )

        return CompanyDiscoveryResult(
            query=provider_response.query,
            total_results=len(items) + len(errors),
            items=items,
            errors=errors,
        )

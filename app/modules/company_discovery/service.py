from sqlalchemy.orm import Session

from app.modules.company_discovery.schemas import (
    CompanyDiscoveryPersistenceResult,
    CompanyDiscoveryRequest,
    CompanyDiscoveryResult,
)
from app.modules.company_discovery.serpapi_adapter import (
    CompanyDiscoveryAdapterError,
    serpapi_result_to_ingestion_item,
)
from app.modules.company_import.ingestion import CompanyIngestionService
from app.modules.company_import.schemas import (
    CompanyIngestionError,
    CompanyIngestionItem,
    CompanyIngestionResult,
)
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

    def discover_and_ingest_from_serpapi(
        self,
        *,
        session: Session,
        project_id: int,
        request: CompanyDiscoveryRequest,
    ) -> CompanyDiscoveryPersistenceResult:
        discovery_result = self.discover_from_serpapi(request)

        if discovery_result.items:
            ingestion_result = CompanyIngestionService(session).ingest(
                project_id,
                discovery_result.items,
            )
        else:
            ingestion_result = CompanyIngestionResult(
                total_rows=0,
                imported=0,
                skipped_duplicates=0,
                failed=0,
                created_company_ids=[],
                duplicates=[],
                errors=[],
                rolled_back=False,
            )

        errors = [*discovery_result.errors, *ingestion_result.errors]

        return CompanyDiscoveryPersistenceResult(
            query=discovery_result.query,
            discovered=discovery_result.total_results,
            imported=ingestion_result.imported,
            skipped_duplicates=ingestion_result.skipped_duplicates,
            failed=len(discovery_result.errors) + ingestion_result.failed,
            created_company_ids=ingestion_result.created_company_ids,
            errors=errors,
            rolled_back=ingestion_result.rolled_back,
        )

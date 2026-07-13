from collections.abc import Callable

from sqlalchemy.orm import Session

from app.modules.company_discovery.profile_execution import SearchProfileDiscoveryService
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.schemas import SearchProfileDiscoveryPersistResult
from app.modules.company_import.ingestion import CompanyIngestionService
from app.modules.search_profile.schemas import SearchProfileRead, SearchProfileRunOptions

IngestionServiceFactory = Callable[[Session], CompanyIngestionService]


class SearchProfileDiscoveryPersistenceError(Exception):
    """Controlled SearchProfile persistence execution failure."""


class SearchProfileDiscoveryPersistenceService:
    """Persist adapted SearchProfile discovery items through the ingestion service."""

    def __init__(
        self,
        discovery_service: SearchProfileDiscoveryService | None = None,
        ingestion_service_factory: IngestionServiceFactory | None = None,
    ) -> None:
        self.discovery_service = discovery_service or SearchProfileDiscoveryService()
        self.ingestion_service_factory = ingestion_service_factory or CompanyIngestionService

    def run_persist(
        self,
        *,
        session: Session,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchProfileDiscoveryPersistResult:
        dry_run_result = self.discovery_service.run_dry(profile, provider, options)
        items = [
            item for query_result in dry_run_result.query_results for item in query_result.items
        ]

        if not items:
            return SearchProfileDiscoveryPersistResult(
                **dry_run_result.model_dump(),
                ingestion_attempted=False,
                total_items_submitted_to_ingestion=0,
            )

        ingestion_service = self.ingestion_service_factory(session)

        try:
            ingestion_result = ingestion_service.ingest(profile.project_id, items)
        except Exception:
            raise SearchProfileDiscoveryPersistenceError("Company ingestion failed.") from None

        return SearchProfileDiscoveryPersistResult(
            **dry_run_result.model_dump(),
            ingestion_attempted=True,
            total_items_submitted_to_ingestion=len(items),
            ingestion_result=ingestion_result,
        )

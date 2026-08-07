from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.company_discovery.candidate_promotion import (
        CompanyDiscoveryCandidateNotEligibleError,
        CompanyDiscoveryCandidatePromotionConsistencyError,
        CompanyDiscoveryCandidatePromotionError,
        CompanyDiscoveryCandidatePromotionInvalidDataError,
        CompanyDiscoveryCandidatePromotionNotFoundError,
        CompanyDiscoveryCandidatePromotionService,
    )
    from app.modules.company_discovery.candidate_promotion_schemas import (
        CompanyDiscoveryCandidatePromotionResult,
    )
    from app.modules.company_discovery.candidate_review import (
        CompanyDiscoveryCandidateReviewNotFoundError,
        CompanyDiscoveryCandidateReviewService,
        CompanyDiscoveryCandidateTransitionError,
    )
    from app.modules.company_discovery.candidate_review_schemas import (
        CompanyDiscoveryCandidateReviewAction,
        CompanyDiscoveryCandidateReviewResult,
    )
    from app.modules.company_discovery.profile_execution import (
        SearchProfileDiscoveryExecutionError,
        SearchProfileDiscoveryService,
    )
    from app.modules.company_discovery.profile_persistence import (
        SearchProfileDiscoveryPersistenceError,
        SearchProfileDiscoveryPersistenceService,
    )
    from app.modules.company_discovery.provider_interfaces import (
        DiscoveryProvider,
        DiscoveryProviderAuthenticationError,
        DiscoveryProviderConfigurationError,
        DiscoveryProviderError,
        DiscoveryProviderQuotaExceededError,
        DiscoveryProviderRateLimitError,
        DiscoveryProviderRequestError,
        DiscoveryProviderResponseError,
        DiscoveryProviderResponseTooLargeError,
    )
    from app.modules.company_discovery.result_adapter import (
        DiscoveryResultAdapterError,
        provider_result_to_ingestion_item,
    )
    from app.modules.company_discovery.schemas import (
        CompanyDiscoveryPersistenceResult,
        CompanyDiscoveryRequest,
        CompanyDiscoveryResult,
        DiscoveryProviderResponse,
        DiscoveryProviderResult,
        SearchProfileDiscoveryAdapterError,
        SearchProfileDiscoveryDryRunResult,
        SearchProfileDiscoveryPersistResult,
        SearchProfileDiscoveryProviderError,
        SearchProfileDiscoveryQueryResult,
    )
    from app.modules.company_discovery.serpapi_adapter import (
        CompanyDiscoveryAdapterError,
        serpapi_result_to_ingestion_item,
    )
    from app.modules.company_discovery.serpapi_provider import SerpApiDiscoveryProvider
    from app.modules.company_discovery.service import CompanyDiscoveryService
    from app.modules.company_discovery.staging_orchestration import CompanyDiscoveryStagingService
    from app.modules.company_discovery.staging_service_schemas import (
        CompanyDiscoveryStagingCandidateDraft,
        CompanyDiscoveryStagingCandidatePreview,
        CompanyDiscoveryStagingRunResult,
    )

__all__ = [
    "CompanyDiscoveryAdapterError",
    "CompanyDiscoveryPersistenceResult",
    "CompanyDiscoveryRequest",
    "CompanyDiscoveryResult",
    "CompanyDiscoveryService",
    "CompanyDiscoveryCandidateReviewAction",
    "CompanyDiscoveryCandidateReviewResult",
    "CompanyDiscoveryCandidateReviewNotFoundError",
    "CompanyDiscoveryCandidateReviewService",
    "CompanyDiscoveryCandidateTransitionError",
    "CompanyDiscoveryCandidateNotEligibleError",
    "CompanyDiscoveryCandidatePromotionConsistencyError",
    "CompanyDiscoveryCandidatePromotionError",
    "CompanyDiscoveryCandidatePromotionInvalidDataError",
    "CompanyDiscoveryCandidatePromotionNotFoundError",
    "CompanyDiscoveryCandidatePromotionResult",
    "CompanyDiscoveryCandidatePromotionService",
    "DiscoveryProvider",
    "DiscoveryProviderAuthenticationError",
    "DiscoveryProviderConfigurationError",
    "DiscoveryProviderError",
    "DiscoveryProviderQuotaExceededError",
    "DiscoveryProviderRateLimitError",
    "DiscoveryProviderRequestError",
    "DiscoveryProviderResponse",
    "DiscoveryProviderResponseError",
    "DiscoveryProviderResponseTooLargeError",
    "DiscoveryProviderResult",
    "DiscoveryResultAdapterError",
    "SearchProfileDiscoveryAdapterError",
    "SearchProfileDiscoveryDryRunResult",
    "SearchProfileDiscoveryExecutionError",
    "SearchProfileDiscoveryPersistenceError",
    "SearchProfileDiscoveryPersistenceService",
    "SearchProfileDiscoveryPersistResult",
    "SearchProfileDiscoveryProviderError",
    "SearchProfileDiscoveryQueryResult",
    "SearchProfileDiscoveryService",
    "CompanyDiscoveryStagingService",
    "CompanyDiscoveryStagingCandidateDraft",
    "CompanyDiscoveryStagingCandidatePreview",
    "CompanyDiscoveryStagingRunResult",
    "SerpApiDiscoveryProvider",
    "provider_result_to_ingestion_item",
    "serpapi_result_to_ingestion_item",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "CompanyDiscoveryCandidateNotEligibleError": (
        "app.modules.company_discovery.candidate_promotion",
        "CompanyDiscoveryCandidateNotEligibleError",
    ),
    "CompanyDiscoveryCandidatePromotionConsistencyError": (
        "app.modules.company_discovery.candidate_promotion",
        "CompanyDiscoveryCandidatePromotionConsistencyError",
    ),
    "CompanyDiscoveryCandidatePromotionError": (
        "app.modules.company_discovery.candidate_promotion",
        "CompanyDiscoveryCandidatePromotionError",
    ),
    "CompanyDiscoveryCandidatePromotionInvalidDataError": (
        "app.modules.company_discovery.candidate_promotion",
        "CompanyDiscoveryCandidatePromotionInvalidDataError",
    ),
    "CompanyDiscoveryCandidatePromotionNotFoundError": (
        "app.modules.company_discovery.candidate_promotion",
        "CompanyDiscoveryCandidatePromotionNotFoundError",
    ),
    "CompanyDiscoveryCandidatePromotionService": (
        "app.modules.company_discovery.candidate_promotion",
        "CompanyDiscoveryCandidatePromotionService",
    ),
    "CompanyDiscoveryCandidatePromotionResult": (
        "app.modules.company_discovery.candidate_promotion_schemas",
        "CompanyDiscoveryCandidatePromotionResult",
    ),
    "CompanyDiscoveryCandidateReviewNotFoundError": (
        "app.modules.company_discovery.candidate_review",
        "CompanyDiscoveryCandidateReviewNotFoundError",
    ),
    "CompanyDiscoveryCandidateReviewService": (
        "app.modules.company_discovery.candidate_review",
        "CompanyDiscoveryCandidateReviewService",
    ),
    "CompanyDiscoveryCandidateTransitionError": (
        "app.modules.company_discovery.candidate_review",
        "CompanyDiscoveryCandidateTransitionError",
    ),
    "CompanyDiscoveryCandidateReviewAction": (
        "app.modules.company_discovery.candidate_review_schemas",
        "CompanyDiscoveryCandidateReviewAction",
    ),
    "CompanyDiscoveryCandidateReviewResult": (
        "app.modules.company_discovery.candidate_review_schemas",
        "CompanyDiscoveryCandidateReviewResult",
    ),
    "SearchProfileDiscoveryExecutionError": (
        "app.modules.company_discovery.profile_execution",
        "SearchProfileDiscoveryExecutionError",
    ),
    "SearchProfileDiscoveryService": (
        "app.modules.company_discovery.profile_execution",
        "SearchProfileDiscoveryService",
    ),
    "SearchProfileDiscoveryPersistenceError": (
        "app.modules.company_discovery.profile_persistence",
        "SearchProfileDiscoveryPersistenceError",
    ),
    "SearchProfileDiscoveryPersistenceService": (
        "app.modules.company_discovery.profile_persistence",
        "SearchProfileDiscoveryPersistenceService",
    ),
    "DiscoveryProvider": ("app.modules.company_discovery.provider_interfaces", "DiscoveryProvider"),
    "DiscoveryProviderAuthenticationError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderAuthenticationError",
    ),
    "DiscoveryProviderConfigurationError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderConfigurationError",
    ),
    "DiscoveryProviderError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderError",
    ),
    "DiscoveryProviderQuotaExceededError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderQuotaExceededError",
    ),
    "DiscoveryProviderRateLimitError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderRateLimitError",
    ),
    "DiscoveryProviderRequestError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderRequestError",
    ),
    "DiscoveryProviderResponseError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderResponseError",
    ),
    "DiscoveryProviderResponseTooLargeError": (
        "app.modules.company_discovery.provider_interfaces",
        "DiscoveryProviderResponseTooLargeError",
    ),
    "DiscoveryResultAdapterError": (
        "app.modules.company_discovery.result_adapter",
        "DiscoveryResultAdapterError",
    ),
    "provider_result_to_ingestion_item": (
        "app.modules.company_discovery.result_adapter",
        "provider_result_to_ingestion_item",
    ),
    "CompanyDiscoveryPersistenceResult": (
        "app.modules.company_discovery.schemas",
        "CompanyDiscoveryPersistenceResult",
    ),
    "CompanyDiscoveryRequest": ("app.modules.company_discovery.schemas", "CompanyDiscoveryRequest"),
    "CompanyDiscoveryResult": ("app.modules.company_discovery.schemas", "CompanyDiscoveryResult"),
    "DiscoveryProviderResponse": (
        "app.modules.company_discovery.schemas",
        "DiscoveryProviderResponse",
    ),
    "DiscoveryProviderResult": ("app.modules.company_discovery.schemas", "DiscoveryProviderResult"),
    "SearchProfileDiscoveryAdapterError": (
        "app.modules.company_discovery.schemas",
        "SearchProfileDiscoveryAdapterError",
    ),
    "SearchProfileDiscoveryDryRunResult": (
        "app.modules.company_discovery.schemas",
        "SearchProfileDiscoveryDryRunResult",
    ),
    "SearchProfileDiscoveryPersistResult": (
        "app.modules.company_discovery.schemas",
        "SearchProfileDiscoveryPersistResult",
    ),
    "SearchProfileDiscoveryProviderError": (
        "app.modules.company_discovery.schemas",
        "SearchProfileDiscoveryProviderError",
    ),
    "SearchProfileDiscoveryQueryResult": (
        "app.modules.company_discovery.schemas",
        "SearchProfileDiscoveryQueryResult",
    ),
    "CompanyDiscoveryAdapterError": (
        "app.modules.company_discovery.serpapi_adapter",
        "CompanyDiscoveryAdapterError",
    ),
    "serpapi_result_to_ingestion_item": (
        "app.modules.company_discovery.serpapi_adapter",
        "serpapi_result_to_ingestion_item",
    ),
    "SerpApiDiscoveryProvider": (
        "app.modules.company_discovery.serpapi_provider",
        "SerpApiDiscoveryProvider",
    ),
    "CompanyDiscoveryService": ("app.modules.company_discovery.service", "CompanyDiscoveryService"),
    "CompanyDiscoveryStagingService": (
        "app.modules.company_discovery.staging_orchestration",
        "CompanyDiscoveryStagingService",
    ),
    "CompanyDiscoveryStagingCandidateDraft": (
        "app.modules.company_discovery.staging_service_schemas",
        "CompanyDiscoveryStagingCandidateDraft",
    ),
    "CompanyDiscoveryStagingCandidatePreview": (
        "app.modules.company_discovery.staging_service_schemas",
        "CompanyDiscoveryStagingCandidatePreview",
    ),
    "CompanyDiscoveryStagingRunResult": (
        "app.modules.company_discovery.staging_service_schemas",
        "CompanyDiscoveryStagingRunResult",
    ),
}


def __getattr__(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

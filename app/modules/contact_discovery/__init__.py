from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.contact_discovery.candidate_promotion import (
        ContactDiscoveryCandidateNotEligibleError,
        ContactDiscoveryCandidatePromotionConsistencyError,
        ContactDiscoveryCandidatePromotionError,
        ContactDiscoveryCandidatePromotionInvalidDataError,
        ContactDiscoveryCandidatePromotionNotFoundError,
        ContactDiscoveryCandidatePromotionService,
    )
    from app.modules.contact_discovery.candidate_promotion_schemas import (
        ContactDiscoveryCandidatePromotionResult,
    )
    from app.modules.contact_discovery.candidate_review import (
        ContactDiscoveryCandidateReviewNotFoundError,
        ContactDiscoveryCandidateReviewService,
        ContactDiscoveryCandidateTransitionError,
    )
    from app.modules.contact_discovery.candidate_review_schemas import (
        ContactDiscoveryCandidateReviewAction,
        ContactDiscoveryCandidateReviewResult,
    )
    from app.modules.contact_discovery.models import (
        CompanyContactDiscoveryState,
        ContactDiscoveryCandidate,
        ContactDiscoveryCandidateStatus,
        ContactDiscoverySourceType,
        ContactDiscoveryStatus,
    )
    from app.modules.contact_discovery.repository import ContactDiscoveryRepository
    from app.modules.contact_discovery.schemas import (
        ContactDiscoveryCandidateCreate,
        ContactDiscoveryCandidateRead,
        ContactDiscoveryCandidateUpdate,
        ContactDiscoveryCandidateUpsertResult,
        ContactDiscoveryStateCreate,
        ContactDiscoveryStateUpdate,
    )
    from app.modules.contact_discovery.service import (
        ContactDiscoveryProvider,
        ContactDiscoveryRunResult,
        ContactDiscoveryService,
    )
    from app.modules.contact_discovery.website_contact_parser import (
        MAX_HTML_LENGTH,
        parse_contact_discovery_candidates_from_html,
    )
    from app.modules.contact_discovery.website_provider import (
        WebsiteContactDiscoveryProvider,
        WebsiteContactDiscoveryProviderResult,
    )

__all__ = [
    "CompanyContactDiscoveryState",
    "ContactDiscoveryCandidateNotEligibleError",
    "ContactDiscoveryCandidatePromotionConsistencyError",
    "ContactDiscoveryCandidatePromotionError",
    "ContactDiscoveryCandidatePromotionInvalidDataError",
    "ContactDiscoveryCandidatePromotionNotFoundError",
    "ContactDiscoveryCandidatePromotionResult",
    "ContactDiscoveryCandidatePromotionService",
    "ContactDiscoveryCandidateReviewAction",
    "ContactDiscoveryCandidateReviewNotFoundError",
    "ContactDiscoveryCandidateReviewResult",
    "ContactDiscoveryCandidateReviewService",
    "ContactDiscoveryCandidateTransitionError",
    "ContactDiscoveryCandidate",
    "ContactDiscoveryCandidateCreate",
    "ContactDiscoveryCandidateRead",
    "ContactDiscoveryCandidateStatus",
    "ContactDiscoveryCandidateUpdate",
    "ContactDiscoveryCandidateUpsertResult",
    "ContactDiscoveryRepository",
    "ContactDiscoveryProvider",
    "ContactDiscoveryRunResult",
    "ContactDiscoveryService",
    "ContactDiscoverySourceType",
    "ContactDiscoveryStateCreate",
    "ContactDiscoveryStateUpdate",
    "ContactDiscoveryStatus",
    "MAX_HTML_LENGTH",
    "WebsiteContactDiscoveryProvider",
    "WebsiteContactDiscoveryProviderResult",
    "parse_contact_discovery_candidates_from_html",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "CompanyContactDiscoveryState": (
        "app.modules.contact_discovery.models",
        "CompanyContactDiscoveryState",
    ),
    "ContactDiscoveryCandidate": (
        "app.modules.contact_discovery.models",
        "ContactDiscoveryCandidate",
    ),
    "ContactDiscoveryCandidateStatus": (
        "app.modules.contact_discovery.models",
        "ContactDiscoveryCandidateStatus",
    ),
    "ContactDiscoverySourceType": (
        "app.modules.contact_discovery.models",
        "ContactDiscoverySourceType",
    ),
    "ContactDiscoveryStatus": ("app.modules.contact_discovery.models", "ContactDiscoveryStatus"),
    "ContactDiscoveryRepository": (
        "app.modules.contact_discovery.repository",
        "ContactDiscoveryRepository",
    ),
    "ContactDiscoveryCandidateCreate": (
        "app.modules.contact_discovery.schemas",
        "ContactDiscoveryCandidateCreate",
    ),
    "ContactDiscoveryCandidateRead": (
        "app.modules.contact_discovery.schemas",
        "ContactDiscoveryCandidateRead",
    ),
    "ContactDiscoveryCandidateUpdate": (
        "app.modules.contact_discovery.schemas",
        "ContactDiscoveryCandidateUpdate",
    ),
    "ContactDiscoveryCandidateUpsertResult": (
        "app.modules.contact_discovery.schemas",
        "ContactDiscoveryCandidateUpsertResult",
    ),
    "ContactDiscoveryStateCreate": (
        "app.modules.contact_discovery.schemas",
        "ContactDiscoveryStateCreate",
    ),
    "ContactDiscoveryStateUpdate": (
        "app.modules.contact_discovery.schemas",
        "ContactDiscoveryStateUpdate",
    ),
    "ContactDiscoveryProvider": (
        "app.modules.contact_discovery.service",
        "ContactDiscoveryProvider",
    ),
    "ContactDiscoveryRunResult": (
        "app.modules.contact_discovery.service",
        "ContactDiscoveryRunResult",
    ),
    "ContactDiscoveryService": ("app.modules.contact_discovery.service", "ContactDiscoveryService"),
    "MAX_HTML_LENGTH": ("app.modules.contact_discovery.website_contact_parser", "MAX_HTML_LENGTH"),
    "parse_contact_discovery_candidates_from_html": (
        "app.modules.contact_discovery.website_contact_parser",
        "parse_contact_discovery_candidates_from_html",
    ),
    "WebsiteContactDiscoveryProvider": (
        "app.modules.contact_discovery.website_provider",
        "WebsiteContactDiscoveryProvider",
    ),
    "WebsiteContactDiscoveryProviderResult": (
        "app.modules.contact_discovery.website_provider",
        "WebsiteContactDiscoveryProviderResult",
    ),
    "ContactDiscoveryCandidateNotEligibleError": (
        "app.modules.contact_discovery.candidate_promotion",
        "ContactDiscoveryCandidateNotEligibleError",
    ),
    "ContactDiscoveryCandidatePromotionConsistencyError": (
        "app.modules.contact_discovery.candidate_promotion",
        "ContactDiscoveryCandidatePromotionConsistencyError",
    ),
    "ContactDiscoveryCandidatePromotionError": (
        "app.modules.contact_discovery.candidate_promotion",
        "ContactDiscoveryCandidatePromotionError",
    ),
    "ContactDiscoveryCandidatePromotionInvalidDataError": (
        "app.modules.contact_discovery.candidate_promotion",
        "ContactDiscoveryCandidatePromotionInvalidDataError",
    ),
    "ContactDiscoveryCandidatePromotionNotFoundError": (
        "app.modules.contact_discovery.candidate_promotion",
        "ContactDiscoveryCandidatePromotionNotFoundError",
    ),
    "ContactDiscoveryCandidatePromotionService": (
        "app.modules.contact_discovery.candidate_promotion",
        "ContactDiscoveryCandidatePromotionService",
    ),
    "ContactDiscoveryCandidatePromotionResult": (
        "app.modules.contact_discovery.candidate_promotion_schemas",
        "ContactDiscoveryCandidatePromotionResult",
    ),
    "ContactDiscoveryCandidateReviewNotFoundError": (
        "app.modules.contact_discovery.candidate_review",
        "ContactDiscoveryCandidateReviewNotFoundError",
    ),
    "ContactDiscoveryCandidateReviewService": (
        "app.modules.contact_discovery.candidate_review",
        "ContactDiscoveryCandidateReviewService",
    ),
    "ContactDiscoveryCandidateTransitionError": (
        "app.modules.contact_discovery.candidate_review",
        "ContactDiscoveryCandidateTransitionError",
    ),
    "ContactDiscoveryCandidateReviewAction": (
        "app.modules.contact_discovery.candidate_review_schemas",
        "ContactDiscoveryCandidateReviewAction",
    ),
    "ContactDiscoveryCandidateReviewResult": (
        "app.modules.contact_discovery.candidate_review_schemas",
        "ContactDiscoveryCandidateReviewResult",
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

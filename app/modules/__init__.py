from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from app.modules.company import models as _company_models  # noqa: F401
from app.modules.company_discovery import models as _company_discovery_models  # noqa: F401
from app.modules.company_enrichment import models as _company_enrichment_models  # noqa: F401
from app.modules.contact import models as _contact_models  # noqa: F401
from app.modules.contact_discovery import models as _contact_discovery_models  # noqa: F401
from app.modules.email_draft import models as _email_draft_models  # noqa: F401
from app.modules.lead import models as _lead_models  # noqa: F401
from app.modules.project import models as _project_models  # noqa: F401
from app.modules.search_profile import models as _search_profile_models  # noqa: F401
from app.modules.task import models as _task_models  # noqa: F401

if TYPE_CHECKING:
    from app.modules.company.models import Company
    from app.modules.company.repository import CompanyRepository
    from app.modules.company.schemas import CompanyCreate, CompanyRead
    from app.modules.company.service import CompanyService
    from app.modules.company_discovery.models import (
        CompanyDiscoveryCandidate,
        CompanyDiscoveryRun,
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
        DiscoveryProviderResponse,
        DiscoveryProviderResult,
        SearchProfileDiscoveryAdapterError,
        SearchProfileDiscoveryDryRunResult,
        SearchProfileDiscoveryPersistResult,
        SearchProfileDiscoveryProviderError,
        SearchProfileDiscoveryQueryResult,
    )
    from app.modules.company_discovery.serpapi_provider import SerpApiDiscoveryProvider
    from app.modules.company_enrichment.models import CompanyEnrichment
    from app.modules.contact.models import Contact
    from app.modules.contact.repository import ContactRepository
    from app.modules.contact.schemas import ContactCreate, ContactRead
    from app.modules.contact.service import ContactService
    from app.modules.contact_discovery.models import (
        CompanyContactDiscoveryState,
        ContactDiscoveryCandidate,
    )
    from app.modules.lead.models import Lead
    from app.modules.lead.repository import LeadRepository
    from app.modules.lead.schemas import LeadCreate, LeadRead
    from app.modules.lead.service import LeadService
    from app.modules.project.models import Project
    from app.modules.project.repository import ProjectRepository
    from app.modules.project.schemas import ProjectCreate, ProjectRead
    from app.modules.project.service import ProjectService
    from app.modules.search_profile.models import SearchProfile
    from app.modules.search_profile.query_generation import (
        SearchProfileQueryGenerationError,
        SearchProfileQueryGenerator,
    )
    from app.modules.search_profile.repository import SearchProfileRepository
    from app.modules.search_profile.schemas import (
        SearchProfileCreate,
        SearchProfileRead,
        SearchProfileRunOptions,
        SearchProfileUpdate,
        SearchQuery,
        SearchQueryPreview,
    )
    from app.modules.search_profile.service import SearchProfileService
    from app.modules.task.models import Task
    from app.modules.task.repository import TaskRepository
    from app.modules.task.schemas import TaskCreate, TaskRead
    from app.modules.task.service import TaskService

__all__ = [
    "Company",
    "CompanyCreate",
    "CompanyRead",
    "CompanyRepository",
    "CompanyService",
    "CompanyEnrichment",
    "CompanyDiscoveryCandidate",
    "CompanyDiscoveryRun",
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
    "Contact",
    "ContactCreate",
    "ContactRead",
    "ContactRepository",
    "ContactService",
    "CompanyContactDiscoveryState",
    "ContactDiscoveryCandidate",
    "Lead",
    "LeadCreate",
    "LeadRead",
    "LeadRepository",
    "LeadService",
    "Project",
    "ProjectCreate",
    "ProjectRead",
    "ProjectRepository",
    "ProjectService",
    "provider_result_to_ingestion_item",
    "SearchProfile",
    "SearchProfileCreate",
    "SearchProfileQueryGenerationError",
    "SearchProfileQueryGenerator",
    "SearchProfileRead",
    "SearchProfileRepository",
    "SearchProfileRunOptions",
    "SearchProfileService",
    "SearchProfileUpdate",
    "SearchQuery",
    "SearchQueryPreview",
    "SerpApiDiscoveryProvider",
    "Task",
    "TaskCreate",
    "TaskRead",
    "TaskRepository",
    "TaskService",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "Company": ("app.modules.company.models", "Company"),
    "CompanyRepository": ("app.modules.company.repository", "CompanyRepository"),
    "CompanyCreate": ("app.modules.company.schemas", "CompanyCreate"),
    "CompanyRead": ("app.modules.company.schemas", "CompanyRead"),
    "CompanyService": ("app.modules.company.service", "CompanyService"),
    "CompanyDiscoveryCandidate": (
        "app.modules.company_discovery.models",
        "CompanyDiscoveryCandidate",
    ),
    "CompanyDiscoveryRun": ("app.modules.company_discovery.models", "CompanyDiscoveryRun"),
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
    "SerpApiDiscoveryProvider": (
        "app.modules.company_discovery.serpapi_provider",
        "SerpApiDiscoveryProvider",
    ),
    "CompanyEnrichment": ("app.modules.company_enrichment.models", "CompanyEnrichment"),
    "Contact": ("app.modules.contact.models", "Contact"),
    "ContactRepository": ("app.modules.contact.repository", "ContactRepository"),
    "ContactCreate": ("app.modules.contact.schemas", "ContactCreate"),
    "ContactRead": ("app.modules.contact.schemas", "ContactRead"),
    "ContactService": ("app.modules.contact.service", "ContactService"),
    "CompanyContactDiscoveryState": (
        "app.modules.contact_discovery.models",
        "CompanyContactDiscoveryState",
    ),
    "ContactDiscoveryCandidate": (
        "app.modules.contact_discovery.models",
        "ContactDiscoveryCandidate",
    ),
    "Lead": ("app.modules.lead.models", "Lead"),
    "LeadRepository": ("app.modules.lead.repository", "LeadRepository"),
    "LeadCreate": ("app.modules.lead.schemas", "LeadCreate"),
    "LeadRead": ("app.modules.lead.schemas", "LeadRead"),
    "LeadService": ("app.modules.lead.service", "LeadService"),
    "Project": ("app.modules.project.models", "Project"),
    "ProjectRepository": ("app.modules.project.repository", "ProjectRepository"),
    "ProjectCreate": ("app.modules.project.schemas", "ProjectCreate"),
    "ProjectRead": ("app.modules.project.schemas", "ProjectRead"),
    "ProjectService": ("app.modules.project.service", "ProjectService"),
    "SearchProfile": ("app.modules.search_profile.models", "SearchProfile"),
    "SearchProfileQueryGenerationError": (
        "app.modules.search_profile.query_generation",
        "SearchProfileQueryGenerationError",
    ),
    "SearchProfileQueryGenerator": (
        "app.modules.search_profile.query_generation",
        "SearchProfileQueryGenerator",
    ),
    "SearchProfileRepository": ("app.modules.search_profile.repository", "SearchProfileRepository"),
    "SearchProfileCreate": ("app.modules.search_profile.schemas", "SearchProfileCreate"),
    "SearchProfileRead": ("app.modules.search_profile.schemas", "SearchProfileRead"),
    "SearchProfileRunOptions": ("app.modules.search_profile.schemas", "SearchProfileRunOptions"),
    "SearchProfileUpdate": ("app.modules.search_profile.schemas", "SearchProfileUpdate"),
    "SearchQuery": ("app.modules.search_profile.schemas", "SearchQuery"),
    "SearchQueryPreview": ("app.modules.search_profile.schemas", "SearchQueryPreview"),
    "SearchProfileService": ("app.modules.search_profile.service", "SearchProfileService"),
    "Task": ("app.modules.task.models", "Task"),
    "TaskRepository": ("app.modules.task.repository", "TaskRepository"),
    "TaskCreate": ("app.modules.task.schemas", "TaskCreate"),
    "TaskRead": ("app.modules.task.schemas", "TaskRead"),
    "TaskService": ("app.modules.task.service", "TaskService"),
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

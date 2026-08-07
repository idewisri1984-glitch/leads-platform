from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .company_apply import (
        AgentCompanyApplyConfirmationRequiredError,
        AgentCompanyApplyConflictError,
        AgentCompanyApplyConsistencyError,
        AgentCompanyApplyError,
        AgentCompanyApplyInternalError,
        AgentCompanyApplyInvalidDataError,
        AgentCompanyApplyNotEligibleError,
        AgentCompanyApplyNotFoundError,
        AgentCompanyApplyPersistenceError,
        AgentCompanyApplyService,
        AgentCompanyApplyStaleHandoffError,
    )
    from .company_apply_schemas import AgentCompanyApplyInput, AgentCompanyApplyResult
    from .company_plan import (
        AgentCompanyPlanBindingError as AgentCompanyPlanBindingError,
    )
    from .company_plan import (
        AgentCompanyPlanDecisionError as AgentCompanyPlanDecisionError,
    )
    from .company_plan import (
        AgentCompanyPlanDiscoveryDataError as AgentCompanyPlanDiscoveryDataError,
    )
    from .company_plan import (
        AgentCompanyPlanError as AgentCompanyPlanError,
    )
    from .company_plan import (
        AgentCompanyPlanInternalError as AgentCompanyPlanInternalError,
    )
    from .company_plan import (
        AgentCompanyPlanInvalidDataError as AgentCompanyPlanInvalidDataError,
    )
    from .company_plan import (
        AgentCompanyPlanPersistenceError as AgentCompanyPlanPersistenceError,
    )
    from .company_plan import (
        AgentCompanyPlanProjectNotFoundError as AgentCompanyPlanProjectNotFoundError,
    )
    from .company_plan import (
        AgentCompanyPlanSearchProfileNotFoundError as AgentCompanyPlanSearchProfileNotFoundError,
    )
    from .company_plan import (
        AgentCompanyPlanSearchProfileNotReadyError as AgentCompanyPlanSearchProfileNotReadyError,
    )
    from .company_plan import (
        AgentCompanyPlanSearchProviderError as AgentCompanyPlanSearchProviderError,
    )
    from .company_plan import (
        AgentCompanyPlanSelectionError as AgentCompanyPlanSelectionError,
    )
    from .company_plan import (
        AgentCompanyPlanService as AgentCompanyPlanService,
    )
    from .company_plan_schemas import (
        AgentCompanyPlanInput as AgentCompanyPlanInput,
    )
    from .company_plan_schemas import (
        AgentCompanyPlanResult as AgentCompanyPlanResult,
    )
    from .company_selection import (
        AgentCompanySelectionConsistencyError,
        AgentCompanySelectionError,
        AgentCompanySelectionInvalidDataError,
        AgentCompanySelectionNoCandidatesError,
        AgentCompanySelectionRunNotFoundError,
        AgentCompanySelectionRunNotReadyError,
        AgentCompanySelectionService,
    )
    from .company_selection_schemas import (
        AgentCompanySelectionBinding,
        AgentCompanySelectionInput,
    )
    from .contact_apply import (
        AgentContactApplyConfirmationRequiredError,
        AgentContactApplyConflictError,
        AgentContactApplyConsistencyError,
        AgentContactApplyError,
        AgentContactApplyInternalError,
        AgentContactApplyInvalidDataError,
        AgentContactApplyNotEligibleError,
        AgentContactApplyNotFoundError,
        AgentContactApplyPersistenceError,
        AgentContactApplyService,
        AgentContactApplyStaleHandoffError,
    )
    from .contact_apply_schemas import AgentContactApplyInput, AgentContactApplyResult

__all__ = [
    "AgentCompanyApplyConflictError",
    "AgentCompanyApplyConfirmationRequiredError",
    "AgentCompanyApplyConsistencyError",
    "AgentCompanyApplyError",
    "AgentCompanyApplyInput",
    "AgentCompanyApplyInternalError",
    "AgentCompanyApplyInvalidDataError",
    "AgentCompanyApplyNotEligibleError",
    "AgentCompanyApplyNotFoundError",
    "AgentCompanyApplyPersistenceError",
    "AgentCompanyApplyResult",
    "AgentCompanyApplyService",
    "AgentCompanyApplyStaleHandoffError",
    "AgentCompanyPlanBindingError",
    "AgentCompanyPlanDecisionError",
    "AgentCompanyPlanDiscoveryDataError",
    "AgentCompanyPlanError",
    "AgentCompanyPlanInput",
    "AgentCompanyPlanInternalError",
    "AgentCompanyPlanInvalidDataError",
    "AgentCompanyPlanPersistenceError",
    "AgentCompanyPlanProjectNotFoundError",
    "AgentCompanyPlanResult",
    "AgentCompanyPlanSearchProfileNotFoundError",
    "AgentCompanyPlanSearchProfileNotReadyError",
    "AgentCompanyPlanSearchProviderError",
    "AgentCompanyPlanSelectionError",
    "AgentCompanyPlanService",
    "AgentCompanySelectionBinding",
    "AgentCompanySelectionConsistencyError",
    "AgentCompanySelectionError",
    "AgentCompanySelectionInput",
    "AgentCompanySelectionInvalidDataError",
    "AgentCompanySelectionNoCandidatesError",
    "AgentCompanySelectionRunNotFoundError",
    "AgentCompanySelectionRunNotReadyError",
    "AgentCompanySelectionService",
    "AgentContactApplyConflictError",
    "AgentContactApplyConfirmationRequiredError",
    "AgentContactApplyConsistencyError",
    "AgentContactApplyError",
    "AgentContactApplyInput",
    "AgentContactApplyInternalError",
    "AgentContactApplyInvalidDataError",
    "AgentContactApplyNotEligibleError",
    "AgentContactApplyNotFoundError",
    "AgentContactApplyPersistenceError",
    "AgentContactApplyResult",
    "AgentContactApplyService",
    "AgentContactApplyStaleHandoffError",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentCompanyPlanBindingError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanBindingError",
    ),
    "AgentCompanyPlanDecisionError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanDecisionError",
    ),
    "AgentCompanyPlanDiscoveryDataError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanDiscoveryDataError",
    ),
    "AgentCompanyPlanError": ("app.modules.agent.company_plan", "AgentCompanyPlanError"),
    "AgentCompanyPlanInternalError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanInternalError",
    ),
    "AgentCompanyPlanInvalidDataError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanInvalidDataError",
    ),
    "AgentCompanyPlanPersistenceError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanPersistenceError",
    ),
    "AgentCompanyPlanProjectNotFoundError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanProjectNotFoundError",
    ),
    "AgentCompanyPlanSearchProfileNotFoundError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanSearchProfileNotFoundError",
    ),
    "AgentCompanyPlanSearchProfileNotReadyError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanSearchProfileNotReadyError",
    ),
    "AgentCompanyPlanSearchProviderError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanSearchProviderError",
    ),
    "AgentCompanyPlanSelectionError": (
        "app.modules.agent.company_plan",
        "AgentCompanyPlanSelectionError",
    ),
    "AgentCompanyPlanService": ("app.modules.agent.company_plan", "AgentCompanyPlanService"),
    "AgentCompanyPlanInput": ("app.modules.agent.company_plan_schemas", "AgentCompanyPlanInput"),
    "AgentCompanyPlanResult": ("app.modules.agent.company_plan_schemas", "AgentCompanyPlanResult"),
    "AgentCompanySelectionConsistencyError": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionConsistencyError",
    ),
    "AgentCompanySelectionError": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionError",
    ),
    "AgentCompanySelectionInvalidDataError": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionInvalidDataError",
    ),
    "AgentCompanySelectionNoCandidatesError": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionNoCandidatesError",
    ),
    "AgentCompanySelectionRunNotFoundError": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionRunNotFoundError",
    ),
    "AgentCompanySelectionRunNotReadyError": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionRunNotReadyError",
    ),
    "AgentCompanySelectionService": (
        "app.modules.agent.company_selection",
        "AgentCompanySelectionService",
    ),
    "AgentCompanySelectionBinding": (
        "app.modules.agent.company_selection_schemas",
        "AgentCompanySelectionBinding",
    ),
    "AgentCompanySelectionInput": (
        "app.modules.agent.company_selection_schemas",
        "AgentCompanySelectionInput",
    ),
    "AgentCompanyApplyConfirmationRequiredError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyConfirmationRequiredError",
    ),
    "AgentCompanyApplyConflictError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyConflictError",
    ),
    "AgentCompanyApplyConsistencyError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyConsistencyError",
    ),
    "AgentCompanyApplyError": ("app.modules.agent.company_apply", "AgentCompanyApplyError"),
    "AgentCompanyApplyInternalError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyInternalError",
    ),
    "AgentCompanyApplyInvalidDataError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyInvalidDataError",
    ),
    "AgentCompanyApplyNotEligibleError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyNotEligibleError",
    ),
    "AgentCompanyApplyNotFoundError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyNotFoundError",
    ),
    "AgentCompanyApplyPersistenceError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyPersistenceError",
    ),
    "AgentCompanyApplyService": ("app.modules.agent.company_apply", "AgentCompanyApplyService"),
    "AgentCompanyApplyStaleHandoffError": (
        "app.modules.agent.company_apply",
        "AgentCompanyApplyStaleHandoffError",
    ),
    "AgentCompanyApplyInput": ("app.modules.agent.company_apply_schemas", "AgentCompanyApplyInput"),
    "AgentCompanyApplyResult": (
        "app.modules.agent.company_apply_schemas",
        "AgentCompanyApplyResult",
    ),
    "AgentContactApplyConfirmationRequiredError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyConfirmationRequiredError",
    ),
    "AgentContactApplyConflictError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyConflictError",
    ),
    "AgentContactApplyConsistencyError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyConsistencyError",
    ),
    "AgentContactApplyError": ("app.modules.agent.contact_apply", "AgentContactApplyError"),
    "AgentContactApplyInternalError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyInternalError",
    ),
    "AgentContactApplyInvalidDataError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyInvalidDataError",
    ),
    "AgentContactApplyNotEligibleError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyNotEligibleError",
    ),
    "AgentContactApplyNotFoundError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyNotFoundError",
    ),
    "AgentContactApplyPersistenceError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyPersistenceError",
    ),
    "AgentContactApplyService": ("app.modules.agent.contact_apply", "AgentContactApplyService"),
    "AgentContactApplyStaleHandoffError": (
        "app.modules.agent.contact_apply",
        "AgentContactApplyStaleHandoffError",
    ),
    "AgentContactApplyInput": ("app.modules.agent.contact_apply_schemas", "AgentContactApplyInput"),
    "AgentContactApplyResult": (
        "app.modules.agent.contact_apply_schemas",
        "AgentContactApplyResult",
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

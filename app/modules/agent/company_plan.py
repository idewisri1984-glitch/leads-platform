from collections.abc import Callable
from typing import Protocol, cast

from sqlalchemy.exc import SQLAlchemyError

from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.profile_execution import (
    SearchProfileDiscoveryExecutionError,
)
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryStagingServiceError,
)
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingRunResult,
)
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQueryPreview,
)
from app.providers.openai_decision import (
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)

from .company_plan_schemas import AgentCompanyPlanInput, AgentCompanyPlanResult
from .company_selection import (
    AgentCompanySelectionConsistencyError,
    AgentCompanySelectionError,
    AgentCompanySelectionNoCandidatesError,
)
from .company_selection_schemas import AgentCompanySelectionInput

_INVALID = "Agent company plan data is invalid."
_PROJECT_NOT_FOUND = "Project was not found."
_PROFILE_NOT_FOUND = "Search profile was not found."
_PROFILE_NOT_READY = "Search profile is not ready for agent planning."
_PROVIDER_FAILED = "Company search provider failed."
_DISCOVERY_INVALID = "Company discovery results are invalid."
_PERSISTENCE_FAILED = "Company discovery state could not be persisted."
_SELECTION_FAILED = "Agent company selection failed."
_DECISION_FAILED = "Company decision provider failed."
_BINDING_FAILED = "Agent company decision binding is inconsistent."
_INTERNAL_FAILED = "Agent company plan failed."


class AgentCompanyPlanError(ValueError):
    pass


class AgentCompanyPlanInvalidDataError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanProjectNotFoundError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanSearchProfileNotFoundError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanSearchProfileNotReadyError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanSearchProviderError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanDiscoveryDataError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanPersistenceError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanSelectionError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanDecisionError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanBindingError(AgentCompanyPlanError):
    pass


class AgentCompanyPlanInternalError(AgentCompanyPlanError):
    pass


def _translated_call[T](
    operation: Callable[[], T],
    error_type: type[AgentCompanyPlanError],
    message: str,
) -> T:
    translated: AgentCompanyPlanError | None = None
    value: T | None = None
    try:
        value = operation()
    except Exception:
        translated = error_type(message)
    if translated is not None:
        raise translated
    return cast(T, value)


def _run_staging(
    operation: Callable[[], CompanyDiscoveryStagingRunResult],
) -> CompanyDiscoveryStagingRunResult:
    translated: AgentCompanyPlanError | None = None
    result: CompanyDiscoveryStagingRunResult | None = None
    try:
        result = operation()
    except SQLAlchemyError:
        translated = AgentCompanyPlanPersistenceError(_PERSISTENCE_FAILED)
    except CompanyDiscoveryStagingServiceError:
        translated = AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
    except SearchProfileDiscoveryExecutionError:
        translated = AgentCompanyPlanSearchProviderError(_PROVIDER_FAILED)
    except Exception:
        translated = AgentCompanyPlanSearchProviderError(_PROVIDER_FAILED)
    if translated is not None:
        raise translated
    return cast(CompanyDiscoveryStagingRunResult, result)


class ProjectLookup(Protocol):
    def get(self, project_id: int) -> object | None: ...


class SearchProfileLookup(Protocol):
    def get(self, profile_id: int) -> SearchProfileRead | None: ...


class QueryGenerator(Protocol):
    def generate_preview(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchQueryPreview: ...


class StagingOrchestrator(Protocol):
    def run(
        self,
        *,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
        dry_run: bool,
        repository: CompanyDiscoveryStagingRepository | None = None,
    ) -> CompanyDiscoveryStagingRunResult: ...


class DiscoveryCommitter(Protocol):
    def commit_discovery(self) -> None: ...


class SelectionBoundary(Protocol):
    def prepare(
        self,
        *,
        project_id: int,
        run_id: int,
        goal: str,
        max_candidates: int = 5,
    ) -> AgentCompanySelectionInput: ...

    def resolve_selected_candidate_id(
        self,
        selection: AgentCompanySelectionInput,
        decision: OpenAIDecisionResult,
    ) -> int | None: ...


class DecisionBoundary(Protocol):
    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult: ...


class AgentCompanyPlanService:
    def __init__(
        self,
        *,
        projects: ProjectLookup,
        profiles: SearchProfileLookup,
        query_generator: QueryGenerator,
        staging: StagingOrchestrator,
        staging_provider: DiscoveryProvider,
        staging_repository: CompanyDiscoveryStagingRepository,
        committer: DiscoveryCommitter,
        selection: SelectionBoundary,
        decision: DecisionBoundary,
    ) -> None:
        self.projects = projects
        self.profiles = profiles
        self.query_generator = query_generator
        self.staging = staging
        self.staging_provider = staging_provider
        self.staging_repository = staging_repository
        self.committer = committer
        self.selection = selection
        self.decision = decision

    def plan(self, plan_input: AgentCompanyPlanInput) -> AgentCompanyPlanResult:
        data = self._validate_input(plan_input)
        project = _translated_call(
            lambda: self.projects.get(data.project_id),
            AgentCompanyPlanInternalError,
            _INTERNAL_FAILED,
        )
        if project is None:
            raise AgentCompanyPlanProjectNotFoundError(_PROJECT_NOT_FOUND)
        profile = _translated_call(
            lambda: self.profiles.get(data.search_profile_id),
            AgentCompanyPlanInternalError,
            _INTERNAL_FAILED,
        )
        if profile is None or profile.project_id != data.project_id:
            raise AgentCompanyPlanSearchProfileNotFoundError(_PROFILE_NOT_FOUND)
        if not profile.enabled:
            raise AgentCompanyPlanSearchProfileNotReadyError(_PROFILE_NOT_READY)

        options = SearchProfileRunOptions(
            max_queries=1,
            result_limit_per_query=5,
            total_result_ceiling=5,
        )
        preview = _translated_call(
            lambda: self.query_generator.generate_preview(profile, options),
            AgentCompanyPlanSearchProfileNotReadyError,
            _PROFILE_NOT_READY,
        )
        if (
            preview.query_count != 1
            or preview.estimated_provider_requests != 1
            or len(preview.queries) != 1
        ):
            raise AgentCompanyPlanSearchProfileNotReadyError(_PROFILE_NOT_READY)
        query = preview.queries[0].text

        raw_staging = _run_staging(
            lambda: self.staging.run(
                profile=profile,
                provider=self.staging_provider,
                options=options,
                dry_run=False,
                repository=self.staging_repository,
            )
        )
        staging = _translated_call(
            lambda: CompanyDiscoveryStagingRunResult.model_validate(raw_staging.model_dump()),
            AgentCompanyPlanDiscoveryDataError,
            _DISCOVERY_INVALID,
        )
        if staging.query_count != 1:
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        if not staging.run_persisted or staging.run_id is None:
            raise AgentCompanyPlanPersistenceError(_PERSISTENCE_FAILED)
        _translated_call(
            self.committer.commit_discovery,
            AgentCompanyPlanPersistenceError,
            _PERSISTENCE_FAILED,
        )

        if staging.status is CompanyDiscoveryRunStatus.FAILED:
            raise AgentCompanyPlanSearchProviderError(_PROVIDER_FAILED)
        if staging.executed_queries != 1:
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        if staging.status is CompanyDiscoveryRunStatus.NOT_FOUND:
            return self._no_decision(data, staging, query)

        selection_error: AgentCompanyPlanSelectionError | None = None
        try:
            selection = self.selection.prepare(
                project_id=data.project_id,
                run_id=staging.run_id,
                goal=data.goal,
                max_candidates=5,
            )
        except AgentCompanySelectionNoCandidatesError:
            return self._no_decision(data, staging, query)
        except AgentCompanySelectionError:
            selection_error = AgentCompanyPlanSelectionError(_SELECTION_FAILED)
        if selection_error is not None:
            raise selection_error

        eligible_count = len(selection.request.candidates)
        if not 1 <= eligible_count <= 5:
            raise AgentCompanyPlanSelectionError(_SELECTION_FAILED)
        raw_decision = _translated_call(
            lambda: self.decision.decide(selection.request),
            AgentCompanyPlanDecisionError,
            _DECISION_FAILED,
        )
        decision = _translated_call(
            lambda: OpenAIDecisionResult.model_validate(raw_decision.model_dump()),
            AgentCompanyPlanDecisionError,
            _DECISION_FAILED,
        )
        binding_error: AgentCompanyPlanBindingError | None = None
        try:
            selected_id = self.selection.resolve_selected_candidate_id(selection, decision)
        except (AgentCompanySelectionConsistencyError, AgentCompanySelectionError):
            binding_error = AgentCompanyPlanBindingError(_BINDING_FAILED)
        if binding_error is not None:
            raise binding_error
        return _translated_call(
            lambda: self._decision_result(
                data,
                staging,
                query,
                eligible_count,
                decision,
                selected_id,
            ),
            AgentCompanyPlanBindingError,
            _BINDING_FAILED,
        )

    @staticmethod
    def _validate_input(plan_input: AgentCompanyPlanInput) -> AgentCompanyPlanInput:
        if type(plan_input) is not AgentCompanyPlanInput:
            raise AgentCompanyPlanInvalidDataError(_INVALID)
        return _translated_call(
            lambda: AgentCompanyPlanInput.model_validate(plan_input.model_dump()),
            AgentCompanyPlanInvalidDataError,
            _INVALID,
        )

    @staticmethod
    def _no_decision(
        data: AgentCompanyPlanInput,
        staging: CompanyDiscoveryStagingRunResult,
        query: str,
    ) -> AgentCompanyPlanResult:
        return AgentCompanyPlanResult(
            project_id=data.project_id,
            search_profile_id=data.search_profile_id,
            discovery_run_id=cast(int, staging.run_id),
            query=query,
            discovery_run_status=staging.status,
            staged_candidate_count=staging.candidate_upserts,
            eligible_candidate_count=0,
            decision=None,
            selected_candidate_id=None,
            selected_candidate_index=None,
            confidence=None,
            company_fit=None,
            rationale=None,
            next_action_title=None,
            next_action_description=None,
            human_review_required=None,
            serpapi_call_count=1,
            openai_call_count=0,
            crm_mutated=False,
            candidate_promoted=False,
        )

    @staticmethod
    def _decision_result(
        data: AgentCompanyPlanInput,
        staging: CompanyDiscoveryStagingRunResult,
        query: str,
        eligible_count: int,
        decision: OpenAIDecisionResult,
        selected_id: int | None,
    ) -> AgentCompanyPlanResult:
        return AgentCompanyPlanResult(
            project_id=data.project_id,
            search_profile_id=data.search_profile_id,
            discovery_run_id=cast(int, staging.run_id),
            query=query,
            discovery_run_status=staging.status,
            staged_candidate_count=staging.candidate_upserts,
            eligible_candidate_count=eligible_count,
            decision=decision.decision,
            selected_candidate_id=selected_id,
            selected_candidate_index=decision.selected_candidate_index,
            confidence=decision.confidence,
            company_fit=decision.company_fit,
            rationale=decision.rationale,
            next_action_title=decision.next_action_title,
            next_action_description=decision.next_action_description,
            human_review_required=decision.human_review_required,
            serpapi_call_count=1,
            openai_call_count=1,
            crm_mutated=False,
            candidate_promoted=False,
        )


__all__ = [
    "AgentCompanyPlanBindingError",
    "AgentCompanyPlanDecisionError",
    "AgentCompanyPlanDiscoveryDataError",
    "AgentCompanyPlanError",
    "AgentCompanyPlanInternalError",
    "AgentCompanyPlanInvalidDataError",
    "AgentCompanyPlanPersistenceError",
    "AgentCompanyPlanProjectNotFoundError",
    "AgentCompanyPlanSearchProfileNotFoundError",
    "AgentCompanyPlanSearchProfileNotReadyError",
    "AgentCompanyPlanSearchProviderError",
    "AgentCompanyPlanSelectionError",
    "AgentCompanyPlanService",
]

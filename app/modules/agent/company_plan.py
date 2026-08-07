from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.profile_execution import (
    SearchProfileDiscoveryExecutionError,
)
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryBoundedPlanRunResult,
    CompanyDiscoveryStagingServiceError,
)
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingRunResult,
)
from app.modules.search_profile.schemas import SearchProfileRead, SearchProfileRunOptions

if TYPE_CHECKING:
    from app.providers.openai_decision import OpenAIDecisionRequest, OpenAIDecisionResult

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
_DISCOVERY_DATA_ERROR_CODES = frozenset(
    {"candidate_invalid", "execution_invalid", "execution_failed"}
)


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


@dataclass(frozen=True, slots=True)
class _ProjectSnapshot:
    id: int


class _SearchProfileSnapshot(SearchProfileRead):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


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


def _strict_utf8(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError
    value.encode("utf-8")
    return value


class ProjectRecord(Protocol):
    id: object


class ProjectLookup(Protocol):
    def get(self, project_id: int) -> object | None: ...


class SearchProfileLookup(Protocol):
    def get(self, profile_id: int) -> SearchProfileRead | None: ...


class StagingOrchestrator(Protocol):
    def run_bounded_plan(
        self,
        *,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
        dry_run: bool,
        repository: CompanyDiscoveryStagingRepository | None = None,
    ) -> CompanyDiscoveryBoundedPlanRunResult: ...


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

    def revalidate_selection_input(
        self,
        selection: AgentCompanySelectionInput,
    ) -> AgentCompanySelectionInput: ...

    def resolve_selected_candidate_id(
        self,
        selection: AgentCompanySelectionInput,
        decision: OpenAIDecisionResult,
    ) -> int | None: ...


class DecisionBoundary(Protocol):
    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult: ...


class DecisionFactory(Protocol):
    def __call__(self) -> DecisionBoundary: ...


class ProviderTelemetry(Protocol):
    def snapshot_call_count(self) -> int: ...

    def last_query(self) -> str | None: ...


class AgentCompanyPlanService:
    def __init__(
        self,
        *,
        projects: ProjectLookup,
        profiles: SearchProfileLookup,
        staging: StagingOrchestrator,
        staging_provider: DiscoveryProvider,
        staging_repository: CompanyDiscoveryStagingRepository,
        provider_telemetry: ProviderTelemetry,
        committer: DiscoveryCommitter,
        selection: SelectionBoundary,
        decision_factory: DecisionFactory,
    ) -> None:
        self.projects = projects
        self.profiles = profiles
        self.staging = staging
        self.staging_provider = staging_provider
        self.staging_repository = staging_repository
        self.provider_telemetry = provider_telemetry
        self.committer = committer
        self.selection = selection
        self.decision_factory = decision_factory

    def plan(self, plan_input: AgentCompanyPlanInput) -> AgentCompanyPlanResult:
        from app.providers.openai_decision import OpenAIDecisionResult

        data = self._validate_input(plan_input)
        project_record = _translated_call(
            lambda: self.projects.get(data.project_id),
            AgentCompanyPlanInternalError,
            _INTERNAL_FAILED,
        )
        if project_record is None:
            raise AgentCompanyPlanProjectNotFoundError(_PROJECT_NOT_FOUND)
        self._snapshot_project(cast(ProjectRecord, project_record), data.project_id)

        profile_record = _translated_call(
            lambda: self.profiles.get(data.search_profile_id),
            AgentCompanyPlanInternalError,
            _INTERNAL_FAILED,
        )
        if profile_record is None:
            raise AgentCompanyPlanSearchProfileNotFoundError(_PROFILE_NOT_FOUND)
        profile = self._snapshot_profile(profile_record)
        if profile.id != data.search_profile_id or profile.project_id != data.project_id:
            raise AgentCompanyPlanSearchProfileNotFoundError(_PROFILE_NOT_FOUND)
        if not profile.enabled:
            raise AgentCompanyPlanSearchProfileNotReadyError(_PROFILE_NOT_READY)

        options = SearchProfileRunOptions(
            max_queries=1,
            result_limit_per_query=5,
            total_result_ceiling=5,
        )
        before_calls = self._telemetry_count()
        bounded_staging = self._run_staging(profile, options)
        after_calls = self._telemetry_count()
        provider_call_count = after_calls - before_calls
        try:
            query = _strict_utf8(self.provider_telemetry.last_query())
        except (TypeError, ValueError, UnicodeEncodeError):
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID) from None

        staging = self._snapshot_staging(bounded_staging)
        try:
            authoritative_query = _strict_utf8(bounded_staging.query)
        except (TypeError, ValueError, UnicodeEncodeError):
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID) from None
        if (
            provider_call_count != 1
            or authoritative_query != query
            or staging.project_id != data.project_id
            or staging.search_profile_id != data.search_profile_id
            or staging.query_count != 1
            or staging.executed_queries != 1
        ):
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        if not staging.run_persisted or staging.run_id is None:
            raise AgentCompanyPlanPersistenceError(_PERSISTENCE_FAILED)

        _translated_call(
            self.committer.commit_discovery,
            AgentCompanyPlanPersistenceError,
            _PERSISTENCE_FAILED,
        )

        if staging.status is CompanyDiscoveryRunStatus.FAILED:
            if staging.error_code in _DISCOVERY_DATA_ERROR_CODES:
                raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
            raise AgentCompanyPlanSearchProviderError(_PROVIDER_FAILED)
        if (
            staging.status is CompanyDiscoveryRunStatus.PARTIAL
            and staging.error_code in _DISCOVERY_DATA_ERROR_CODES
        ):
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)

        selection_error: AgentCompanyPlanSelectionError | None = None
        try:
            raw_selection = self.selection.prepare(
                project_id=data.project_id,
                run_id=staging.run_id,
                goal=data.goal,
                max_candidates=5,
            )
        except AgentCompanySelectionNoCandidatesError:
            return self._no_decision(data, staging, query, provider_call_count)
        except AgentCompanySelectionError:
            selection_error = AgentCompanyPlanSelectionError(_SELECTION_FAILED)
        if selection_error is not None:
            raise selection_error

        validated_selection = _translated_call(
            lambda: self.selection.revalidate_selection_input(raw_selection),
            AgentCompanyPlanSelectionError,
            _SELECTION_FAILED,
        )
        if (
            validated_selection.project_id != data.project_id
            or validated_selection.run_id != staging.run_id
        ):
            raise AgentCompanyPlanSelectionError(_SELECTION_FAILED)
        eligible_count = len(validated_selection.request.candidates)
        if not 1 <= eligible_count <= 5:
            raise AgentCompanyPlanSelectionError(_SELECTION_FAILED)

        decision_boundary = _translated_call(
            self.decision_factory,
            AgentCompanyPlanDecisionError,
            _DECISION_FAILED,
        )
        raw_decision = _translated_call(
            lambda: decision_boundary.decide(validated_selection.request),
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
            selected_id = self.selection.resolve_selected_candidate_id(
                validated_selection, decision
            )
        except (AgentCompanySelectionConsistencyError, AgentCompanySelectionError):
            binding_error = AgentCompanyPlanBindingError(_BINDING_FAILED)
        if binding_error is not None:
            raise binding_error
        return _translated_call(
            lambda: self._decision_result(
                data,
                staging,
                query,
                provider_call_count,
                eligible_count,
                decision,
                selected_id,
            ),
            AgentCompanyPlanBindingError,
            _BINDING_FAILED,
        )

    def _run_staging(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions,
    ) -> CompanyDiscoveryBoundedPlanRunResult:
        translated: AgentCompanyPlanError | None = None
        result: CompanyDiscoveryBoundedPlanRunResult | None = None
        try:
            result = self.staging.run_bounded_plan(
                profile=profile,
                provider=self.staging_provider,
                options=options,
                dry_run=False,
                repository=self.staging_repository,
            )
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
        return cast(CompanyDiscoveryBoundedPlanRunResult, result)

    def _telemetry_count(self) -> int:
        count = _translated_call(
            self.provider_telemetry.snapshot_call_count,
            AgentCompanyPlanDiscoveryDataError,
            _DISCOVERY_INVALID,
        )
        if type(count) is not int or count < 0:
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        return count

    @staticmethod
    def _snapshot_project(record: ProjectRecord, expected_id: int) -> _ProjectSnapshot:
        invalid = False
        value: object = None
        try:
            value = record.id
        except AttributeError:
            invalid = True
        if invalid or type(value) is not int or value != expected_id:
            raise AgentCompanyPlanProjectNotFoundError(_PROJECT_NOT_FOUND)
        return _ProjectSnapshot(id=value)

    @staticmethod
    def _snapshot_profile(record: SearchProfileRead) -> _SearchProfileSnapshot:
        snapshot = _translated_call(
            lambda: _SearchProfileSnapshot(
                **{field: getattr(record, field) for field in SearchProfileRead.model_fields}
            ),
            AgentCompanyPlanSearchProfileNotReadyError,
            _PROFILE_NOT_READY,
        )
        return snapshot.model_copy(deep=True)

    @staticmethod
    def _snapshot_staging(
        bounded: CompanyDiscoveryBoundedPlanRunResult,
    ) -> CompanyDiscoveryStagingRunResult:
        if type(bounded) is not CompanyDiscoveryBoundedPlanRunResult:
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        raw = bounded.staging_result
        if type(raw) is not CompanyDiscoveryStagingRunResult:
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        try:
            raw_run_id = raw.run_id
        except AttributeError:
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID) from None
        if raw_run_id is not None and (type(raw_run_id) is not int or raw_run_id <= 0):
            raise AgentCompanyPlanDiscoveryDataError(_DISCOVERY_INVALID)
        return _translated_call(
            lambda: CompanyDiscoveryStagingRunResult.model_validate(raw.model_dump()),
            AgentCompanyPlanDiscoveryDataError,
            _DISCOVERY_INVALID,
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
        provider_call_count: int,
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
            serpapi_call_count=provider_call_count,
            openai_call_count=0,
            crm_mutated=False,
            candidate_promoted=False,
        )

    @staticmethod
    def _decision_result(
        data: AgentCompanyPlanInput,
        staging: CompanyDiscoveryStagingRunResult,
        query: str,
        provider_call_count: int,
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
            serpapi_call_count=provider_call_count,
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

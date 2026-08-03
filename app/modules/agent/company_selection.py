from collections.abc import Sequence
from contextlib import suppress
from typing import Protocol, cast

from pydantic import ValidationError

from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.providers.openai_decision import (
    OpenAIDecisionCandidate,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)

from .company_selection_schemas import (
    AgentCompanySelectionBinding,
    AgentCompanySelectionInput,
)

_INVALID_DATA_MESSAGE = "Agent company selection data is invalid."
_RUN_NOT_FOUND_MESSAGE = "Discovery run was not found."
_RUN_NOT_READY_MESSAGE = "Discovery run is not ready for agent selection."
_NO_CANDIDATES_MESSAGE = "No eligible discovery candidates were found."
_CONSISTENCY_MESSAGE = "Agent company selection state is inconsistent."


class AgentCompanySelectionError(ValueError):
    pass


class AgentCompanySelectionInvalidDataError(AgentCompanySelectionError):
    pass


class AgentCompanySelectionRunNotFoundError(AgentCompanySelectionError):
    pass


class AgentCompanySelectionRunNotReadyError(AgentCompanySelectionError):
    pass


class AgentCompanySelectionNoCandidatesError(AgentCompanySelectionError):
    pass


class AgentCompanySelectionConsistencyError(AgentCompanySelectionError):
    pass


class AgentCompanySelectionRunRecord(Protocol):
    id: object
    project_id: object
    run_status: object


class AgentCompanySelectionCandidateRecord(Protocol):
    id: object
    project_id: object
    last_seen_run_id: object
    name: object
    website: object
    country_code: object
    identity_key: object
    best_position: object
    candidate_status: object
    promoted_company_id: object


class AgentCompanySelectionRepository(Protocol):
    def get_run(self, run_id: int) -> AgentCompanySelectionRunRecord | None: ...

    def list_candidates_for_run(
        self,
        project_id: int,
        run_id: int,
        limit: int,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> Sequence[AgentCompanySelectionCandidateRecord]: ...


class AgentCompanySelectionService:
    def __init__(self, repository: AgentCompanySelectionRepository) -> None:
        self.repository = repository

    def prepare(
        self,
        *,
        project_id: int,
        run_id: int,
        goal: str,
        max_candidates: int = 5,
    ) -> AgentCompanySelectionInput:
        self._validate_direct_input(project_id, run_id, goal, max_candidates)

        run = self.repository.get_run(run_id)
        if run is None:
            raise AgentCompanySelectionRunNotFoundError(_RUN_NOT_FOUND_MESSAGE)
        run_status = self._validate_run(run, project_id, run_id)
        if run_status is CompanyDiscoveryRunStatus.NOT_FOUND:
            raise AgentCompanySelectionNoCandidatesError(_NO_CANDIDATES_MESSAGE)
        if run_status in (CompanyDiscoveryRunStatus.PENDING, CompanyDiscoveryRunStatus.FAILED):
            raise AgentCompanySelectionRunNotReadyError(_RUN_NOT_READY_MESSAGE)

        candidates = self.repository.list_candidates_for_run(
            project_id,
            run_id,
            max_candidates,
            CompanyDiscoveryCandidateStatus.DISCOVERED,
        )
        validated = self._validate_candidates(candidates, project_id, run_id, max_candidates)
        if not validated:
            raise AgentCompanySelectionNoCandidatesError(_NO_CANDIDATES_MESSAGE)

        ordered = sorted(validated, key=self._candidate_sort_key)[:max_candidates]
        openai_candidates: list[OpenAIDecisionCandidate] = []
        bindings: list[AgentCompanySelectionBinding] = []
        construction_failed = False
        try:
            for index, candidate in enumerate(ordered, start=1):
                openai_candidates.append(
                    OpenAIDecisionCandidate(
                        index=index,
                        name=self._normalize_name(cast(str, candidate.name)),
                        website=cast(str | None, candidate.website),
                        country=cast(str | None, candidate.country_code),
                        city=None,
                        industry=None,
                        snippet=None,
                        website_summary=None,
                    )
                )
                bindings.append(
                    AgentCompanySelectionBinding(
                        index=index,
                        candidate_id=cast(int, candidate.id),
                    )
                )
            request = OpenAIDecisionRequest(goal=goal, candidates=tuple(openai_candidates))
            result = AgentCompanySelectionInput(
                project_id=project_id,
                run_id=run_id,
                request=request,
                bindings=tuple(bindings),
            )
        except (TypeError, ValueError, ValidationError):
            construction_failed = True
        if construction_failed:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return result

    def resolve_selected_candidate_id(
        self,
        selection: AgentCompanySelectionInput,
        decision: OpenAIDecisionResult,
    ) -> int | None:
        if (
            type(selection) is not AgentCompanySelectionInput
            or type(decision) is not OpenAIDecisionResult
        ):
            raise AgentCompanySelectionInvalidDataError(_INVALID_DATA_MESSAGE)

        validation_failed = False
        try:
            validated_selection = AgentCompanySelectionInput(
                project_id=selection.project_id,
                run_id=selection.run_id,
                request=selection.request,
                bindings=selection.bindings,
            )
            validated_decision = OpenAIDecisionResult(
                decision=decision.decision,
                selected_candidate_index=decision.selected_candidate_index,
                confidence=decision.confidence,
                company_fit=decision.company_fit,
                rationale=decision.rationale,
                next_action_title=decision.next_action_title,
                next_action_description=decision.next_action_description,
                human_review_required=decision.human_review_required,
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            validation_failed = True
        if validation_failed:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

        if validated_decision.decision is OpenAIDecisionKind.NO_SELECTION:
            return None
        if validated_decision.decision is not OpenAIDecisionKind.SELECT:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

        selected_index = validated_decision.selected_candidate_index
        matches = tuple(
            binding for binding in validated_selection.bindings if binding.index == selected_index
        )
        if len(matches) != 1:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return matches[0].candidate_id

    @staticmethod
    def _validate_direct_input(
        project_id: object,
        run_id: object,
        goal: object,
        max_candidates: object,
    ) -> None:
        if (
            type(project_id) is not int
            or project_id <= 0
            or type(run_id) is not int
            or run_id <= 0
            or type(goal) is not str
            or not goal.strip()
            or len(goal) > 1000
            or type(max_candidates) is not int
            or not 1 <= max_candidates <= 5
        ):
            raise AgentCompanySelectionInvalidDataError(_INVALID_DATA_MESSAGE)

    @classmethod
    def _validate_run(
        cls,
        run: AgentCompanySelectionRunRecord,
        project_id: int,
        run_id: int,
    ) -> CompanyDiscoveryRunStatus:
        if type(run.id) is not int or run.id <= 0 or run.id != run_id:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        if type(run.project_id) is not int or run.project_id <= 0:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        if run.project_id != project_id:
            raise AgentCompanySelectionRunNotFoundError(_RUN_NOT_FOUND_MESSAGE)
        status = cls._normalize_run_status(run.run_status)
        if status not in (
            CompanyDiscoveryRunStatus.SUCCEEDED,
            CompanyDiscoveryRunStatus.PARTIAL,
            CompanyDiscoveryRunStatus.NOT_FOUND,
            CompanyDiscoveryRunStatus.PENDING,
            CompanyDiscoveryRunStatus.FAILED,
        ):
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return status

    @staticmethod
    def _normalize_run_status(value: object) -> CompanyDiscoveryRunStatus:
        if type(value) is CompanyDiscoveryRunStatus:
            return value
        if type(value) is str:
            try:
                return CompanyDiscoveryRunStatus(value)
            except ValueError:
                pass
        raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

    @classmethod
    def _validate_candidates(
        cls,
        candidates: object,
        project_id: int,
        run_id: int,
        max_candidates: int,
    ) -> list[AgentCompanySelectionCandidateRecord]:
        if isinstance(candidates, str | bytes | bytearray) or not isinstance(candidates, Sequence):
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        if len(candidates) > max_candidates:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

        validated: list[AgentCompanySelectionCandidateRecord] = []
        candidate_ids: set[int] = set()
        identity_keys: set[str] = set()
        for candidate in candidates:
            cls._validate_candidate(candidate, project_id, run_id)
            if candidate.id in candidate_ids or candidate.identity_key in identity_keys:
                raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
            candidate_ids.add(candidate.id)
            identity_keys.add(candidate.identity_key)
            validated.append(candidate)
        return validated

    @classmethod
    def _validate_candidate(
        cls,
        candidate: AgentCompanySelectionCandidateRecord,
        project_id: int,
        run_id: int,
    ) -> None:
        candidate_values: tuple[object, ...] | None = None
        with suppress(AttributeError):
            candidate_values = (
                candidate.id,
                candidate.project_id,
                candidate.last_seen_run_id,
                candidate.name,
                candidate.website,
                candidate.country_code,
                candidate.identity_key,
                candidate.best_position,
                candidate.candidate_status,
                candidate.promoted_company_id,
            )
        if candidate_values is None:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        (
            candidate_id,
            candidate_project_id,
            last_seen_run_id,
            name,
            website,
            country_code,
            identity_key,
            best_position,
            candidate_status,
            promoted_company_id,
        ) = candidate_values

        valid_country = country_code is None or (
            type(country_code) is str
            and len(country_code) == 2
            and country_code.isascii()
            and country_code.isalpha()
            and country_code == country_code.upper()
        )
        if (
            type(candidate_id) is not int
            or candidate_id <= 0
            or type(candidate_project_id) is not int
            or candidate_project_id <= 0
            or candidate_project_id != project_id
            or type(last_seen_run_id) is not int
            or last_seen_run_id <= 0
            or last_seen_run_id != run_id
            or cls._normalize_candidate_status(candidate_status)
            is not CompanyDiscoveryCandidateStatus.DISCOVERED
            or promoted_company_id is not None
            or type(name) is not str
            or not name.strip()
            or "\x00" in name
            or "<" in name
            or ">" in name
            or (website is not None and type(website) is not str)
            or not valid_country
            or type(identity_key) is not str
            or not identity_key.strip()
            or "\x00" in identity_key
            or (
                best_position is not None and (type(best_position) is not int or best_position <= 0)
            )
        ):
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

    @staticmethod
    def _normalize_candidate_status(value: object) -> CompanyDiscoveryCandidateStatus:
        if type(value) is CompanyDiscoveryCandidateStatus:
            return value
        if type(value) is str:
            try:
                return CompanyDiscoveryCandidateStatus(value)
            except ValueError:
                pass
        raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) > 200:
            normalized = normalized[:197].rstrip() + "..."
        if not normalized:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return normalized

    @staticmethod
    def _candidate_sort_key(
        candidate: AgentCompanySelectionCandidateRecord,
    ) -> tuple[bool, int, str, int]:
        position = cast(int | None, candidate.best_position)
        return (
            position is None,
            position if position is not None else 0,
            cast(str, candidate.identity_key),
            cast(int, candidate.id),
        )

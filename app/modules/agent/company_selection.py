import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _RunSnapshot:
    id: object
    project_id: object
    run_status: object


@dataclass(frozen=True, slots=True)
class _CandidateSnapshot:
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


_MAX_OPENAI_REQUEST_BYTES = 20_000


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
        run_snapshot = self._snapshot_run(run)
        run_status = self._validate_run(run_snapshot, project_id, run_id)
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
        candidate_snapshots = self._snapshot_candidate_collection(candidates)
        validated = self._validate_candidates(
            candidate_snapshots,
            project_id,
            run_id,
            max_candidates,
        )
        if not validated:
            raise AgentCompanySelectionNoCandidatesError(_NO_CANDIDATES_MESSAGE)

        ordered = sorted(validated, key=self._candidate_sort_key)[:max_candidates]
        openai_candidates: list[OpenAIDecisionCandidate] = []
        bindings: list[AgentCompanySelectionBinding] = []
        request: OpenAIDecisionRequest | None = None
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
        except (TypeError, ValueError, ValidationError):
            construction_failed = True
        if construction_failed or request is None:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

        serialized_request = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(serialized_request.encode("utf-8")) > _MAX_OPENAI_REQUEST_BYTES:
            raise AgentCompanySelectionInvalidDataError(_INVALID_DATA_MESSAGE)

        result: AgentCompanySelectionInput | None = None
        input_construction_failed = False
        try:
            result = AgentCompanySelectionInput(
                project_id=project_id,
                run_id=run_id,
                request=request,
                bindings=tuple(bindings),
            )
        except (TypeError, ValueError, ValidationError):
            input_construction_failed = True
        if input_construction_failed or result is None:
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

        validated_selection = self._deep_validate_selection(selection)
        validated_decision = self._deep_validate_decision(decision)

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

    @staticmethod
    def _snapshot_run(run: AgentCompanySelectionRunRecord) -> _RunSnapshot:
        snapshot_failed = False
        try:
            snapshot = _RunSnapshot(
                id=run.id,
                project_id=run.project_id,
                run_status=run.run_status,
            )
        except AttributeError:
            snapshot_failed = True
        if snapshot_failed:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return snapshot

    @classmethod
    def _validate_run(
        cls,
        run: _RunSnapshot,
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
        candidates: tuple[_CandidateSnapshot, ...],
        project_id: int,
        run_id: int,
        max_candidates: int,
    ) -> list[_CandidateSnapshot]:
        if len(candidates) > max_candidates:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)

        validated: list[_CandidateSnapshot] = []
        candidate_ids: set[int] = set()
        identity_keys: set[str] = set()
        for candidate in candidates:
            cls._validate_candidate(candidate, project_id, run_id)
            candidate_id = cast(int, candidate.id)
            identity_key = cast(str, candidate.identity_key)
            if candidate_id in candidate_ids or identity_key in identity_keys:
                raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
            candidate_ids.add(candidate_id)
            identity_keys.add(identity_key)
            validated.append(candidate)
        return validated

    @classmethod
    def _snapshot_candidate_collection(
        cls,
        candidates: object,
    ) -> tuple[_CandidateSnapshot, ...]:
        if isinstance(candidates, str | bytes | bytearray | Mapping) or not isinstance(
            candidates, Sequence
        ):
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        records = tuple(candidates)
        return tuple(cls._snapshot_candidate(candidate) for candidate in records)

    @staticmethod
    def _snapshot_candidate(candidate: object) -> _CandidateSnapshot:
        snapshot_failed = False
        try:
            record = cast(AgentCompanySelectionCandidateRecord, candidate)
            snapshot = _CandidateSnapshot(
                id=record.id,
                project_id=record.project_id,
                last_seen_run_id=record.last_seen_run_id,
                name=record.name,
                website=record.website,
                country_code=record.country_code,
                identity_key=record.identity_key,
                best_position=record.best_position,
                candidate_status=record.candidate_status,
                promoted_company_id=record.promoted_company_id,
            )
        except AttributeError:
            snapshot_failed = True
        if snapshot_failed:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return snapshot

    @classmethod
    def _validate_candidate(
        cls,
        candidate: _CandidateSnapshot,
        project_id: int,
        run_id: int,
    ) -> None:
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
        ) = (
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
        candidate: _CandidateSnapshot,
    ) -> tuple[bool, int, str, int]:
        position = cast(int | None, candidate.best_position)
        return (
            position is None,
            position if position is not None else 0,
            cast(str, candidate.identity_key),
            cast(int, candidate.id),
        )

    @staticmethod
    def _deep_validate_selection(
        selection: AgentCompanySelectionInput,
    ) -> AgentCompanySelectionInput:
        validation_failed = False
        try:
            project_id = selection.project_id
            run_id = selection.run_id
            request = selection.request
            bindings = selection.bindings
            if type(request) is not OpenAIDecisionRequest or type(bindings) is not tuple:
                raise TypeError

            goal = request.goal
            request_candidates = request.candidates
            if type(request_candidates) is not tuple:
                raise TypeError
            validated_candidates: list[OpenAIDecisionCandidate] = []
            for candidate in request_candidates:
                if type(candidate) is not OpenAIDecisionCandidate:
                    raise TypeError
                validated_candidates.append(
                    OpenAIDecisionCandidate(
                        index=candidate.index,
                        name=candidate.name,
                        website=candidate.website,
                        country=candidate.country,
                        city=candidate.city,
                        industry=candidate.industry,
                        snippet=candidate.snippet,
                        website_summary=candidate.website_summary,
                    )
                )
            validated_request = OpenAIDecisionRequest(
                goal=goal,
                candidates=tuple(validated_candidates),
            )

            validated_bindings: list[AgentCompanySelectionBinding] = []
            for binding in bindings:
                if type(binding) is not AgentCompanySelectionBinding:
                    raise TypeError
                validated_bindings.append(
                    AgentCompanySelectionBinding(
                        index=binding.index,
                        candidate_id=binding.candidate_id,
                    )
                )
            validated = AgentCompanySelectionInput(
                project_id=project_id,
                run_id=run_id,
                request=validated_request,
                bindings=tuple(validated_bindings),
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            validation_failed = True
        if validation_failed:
            raise AgentCompanySelectionConsistencyError(_CONSISTENCY_MESSAGE)
        return validated

    @staticmethod
    def _deep_validate_decision(
        decision: OpenAIDecisionResult,
    ) -> OpenAIDecisionResult:
        validation_failed = False
        try:
            validated = OpenAIDecisionResult(
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
        return validated

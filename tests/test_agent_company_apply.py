from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.modules.agent import (
    AgentCompanyApplyConfirmationRequiredError,
    AgentCompanyApplyConflictError,
    AgentCompanyApplyConsistencyError,
    AgentCompanyApplyInput,
    AgentCompanyApplyInvalidDataError,
    AgentCompanyApplyNotEligibleError,
    AgentCompanyApplyNotFoundError,
    AgentCompanyApplyPersistenceError,
    AgentCompanyApplyResult,
    AgentCompanyApplyService,
    AgentCompanyApplyStaleHandoffError,
)
from app.modules.company_discovery import (
    CompanyDiscoveryCandidatePromotionResult,
    CompanyDiscoveryCandidateReviewResult,
)
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateRead


def _input() -> AgentCompanyApplyInput:
    return AgentCompanyApplyInput(project_id=1, discovery_run_id=2, candidate_id=3, confirmed=True)


def _result(**changes: Any) -> AgentCompanyApplyResult:
    values: dict[str, Any] = {
        "project_id": 1,
        "discovery_run_id": 2,
        "candidate_id": 3,
        "company_id": 4,
        "candidate_status_before": CompanyDiscoveryCandidateStatus.REVIEWED,
        "candidate_status_after": CompanyDiscoveryCandidateStatus.PROMOTED,
        "company_created": True,
        "company_reused": False,
        "candidate_reviewed": False,
        "candidate_promoted": True,
        "crm_mutated": True,
        "network_call_count": 0,
        "contact_mutation_count": 0,
        "lead_mutation_count": 0,
        "task_mutation_count": 0,
        "human_confirmation_required": True,
        "human_confirmation_received": True,
    }
    values.update(changes)
    return AgentCompanyApplyResult(**values)


class _Staging:
    def __init__(self, status: CompanyDiscoveryCandidateStatus) -> None:
        linked = 4 if status is CompanyDiscoveryCandidateStatus.PROMOTED else None
        self.run = SimpleNamespace(
            id=2, project_id=1, run_status=CompanyDiscoveryRunStatus.SUCCEEDED
        )
        self.candidate = SimpleNamespace(
            id=3,
            project_id=1,
            first_seen_run_id=2,
            last_seen_run_id=2,
            name="Acme",
            website="https://acme.example",
            country_code="US",
            candidate_status=status,
            promoted_company_id=linked,
        )
        self.events: list[str] = []

    def get_run(self, run_id: int) -> Any:
        self.events.append("run")
        return self.run

    def get_candidate_for_promotion(self, project_id: int, candidate_id: int) -> Any:
        self.events.append("candidate")
        return self.candidate


class _Companies:
    def __init__(self, staging: _Staging) -> None:
        self.staging = staging
        self.company = SimpleNamespace(id=4, project_id=1)

    def acquire_promotion_scope(self, project_id: int) -> None:
        self.staging.events.append("scope")

    def get_for_project(self, project_id: int, company_id: int) -> Any:
        self.staging.events.append("company")
        return self.company


class _Review:
    def __init__(self, staging: _Staging) -> None:
        self.staging = staging

    def mark_reviewed(self, project_id: int, candidate_id: int) -> Any:
        self.staging.events.append("review")
        self.staging.candidate.candidate_status = CompanyDiscoveryCandidateStatus.REVIEWED
        now = datetime.now(UTC)
        candidate = CompanyDiscoveryCandidateRead(
            id=3,
            project_id=1,
            first_seen_run_id=2,
            last_seen_run_id=2,
            provider="serpapi",
            name="Acme",
            normalized_name="acme",
            website="https://acme.example",
            website_identity="acme.example",
            country_code="US",
            identity_key="website:acme.example",
            best_position=1,
            candidate_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            promoted_company_id=None,
            created_at=now,
            updated_at=now,
        )
        return CompanyDiscoveryCandidateReviewResult(
            candidate=candidate,
            previous_status=CompanyDiscoveryCandidateStatus.DISCOVERED,
            current_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            changed=True,
        )


class _Promotion:
    def __init__(self, staging: _Staging, *, created: bool = True) -> None:
        self.staging = staging
        self.created = created

    def promote(self, project_id: int, candidate_id: int) -> Any:
        self.staging.events.append("promote")
        previous = self.staging.candidate.candidate_status
        changed = previous is not CompanyDiscoveryCandidateStatus.PROMOTED
        self.staging.candidate.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
        self.staging.candidate.promoted_company_id = 4
        return CompanyDiscoveryCandidatePromotionResult(
            candidate_id=3,
            project_id=1,
            company_id=4,
            previous_status=previous,
            current_status=CompanyDiscoveryCandidateStatus.PROMOTED,
            created_company=self.created if changed else False,
            changed=changed,
        )


def _service(
    status: CompanyDiscoveryCandidateStatus,
) -> tuple[AgentCompanyApplyService, _Staging]:
    staging = _Staging(status)
    companies = _Companies(staging)
    return (
        AgentCompanyApplyService(
            staging_repository=staging,
            company_repository=companies,
            review_service=_Review(staging),
            promotion_service=_Promotion(staging),
        ),
        staging,
    )


@pytest.mark.parametrize("field", ["project_id", "discovery_run_id", "candidate_id"])
@pytest.mark.parametrize("value", [0, -1, True, "1"])
def test_apply_input_rejects_invalid_identifiers(field: str, value: object) -> None:
    values: dict[str, object] = {
        "project_id": 1,
        "discovery_run_id": 2,
        "candidate_id": 3,
        "confirmed": True,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        AgentCompanyApplyInput(**values)


@pytest.mark.parametrize("confirmed", [False, 1, "true", None])
def test_apply_input_requires_literal_true(confirmed: object) -> None:
    with pytest.raises(ValidationError):
        AgentCompanyApplyInput(
            project_id=1, discovery_run_id=2, candidate_id=3, confirmed=confirmed
        )


def test_apply_input_is_frozen_and_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        AgentCompanyApplyInput(
            project_id=1, discovery_run_id=2, candidate_id=3, confirmed=True, extra=1
        )
    with pytest.raises(ValidationError):
        _input().project_id = 9


@pytest.mark.parametrize(
    ("changes"),
    [
        {"candidate_status_after": CompanyDiscoveryCandidateStatus.REVIEWED},
        {"company_created": False, "company_reused": False},
        {"company_created": True, "company_reused": True},
        {"network_call_count": 1},
        {"contact_mutation_count": 1},
        {"lead_mutation_count": 1},
        {"task_mutation_count": 1},
        {"candidate_reviewed": True},
        {"candidate_promoted": False},
        {"crm_mutated": False},
    ],
)
def test_apply_result_rejects_inconsistent_state(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _result(**changes)


def test_apply_result_accepts_all_three_valid_lifecycles() -> None:
    assert _result().candidate_promoted is True
    assert (
        _result(
            candidate_status_before=CompanyDiscoveryCandidateStatus.DISCOVERED,
            candidate_reviewed=True,
        ).candidate_reviewed
        is True
    )
    assert (
        _result(
            candidate_status_before=CompanyDiscoveryCandidateStatus.PROMOTED,
            company_created=False,
            company_reused=True,
            candidate_promoted=False,
            crm_mutated=False,
        ).candidate_promoted
        is False
    )


@pytest.mark.parametrize(
    ("status", "reviewed", "promoted", "mutated"),
    [
        (CompanyDiscoveryCandidateStatus.DISCOVERED, True, True, True),
        (CompanyDiscoveryCandidateStatus.REVIEWED, False, True, True),
        (CompanyDiscoveryCandidateStatus.PROMOTED, False, False, False),
    ],
)
def test_service_applies_supported_lifecycle(
    status: CompanyDiscoveryCandidateStatus,
    reviewed: bool,
    promoted: bool,
    mutated: bool,
) -> None:
    service, staging = _service(status)
    result = service.apply(_input())
    assert (result.candidate_reviewed, result.candidate_promoted, result.crm_mutated) == (
        reviewed,
        promoted,
        mutated,
    )
    assert result.company_id == 4
    assert staging.events[0:3] == ["scope", "run", "candidate"]
    assert staging.events[-2:] == ["candidate", "company"]


def test_service_rejects_unconfirmed_constructed_input_before_dependencies() -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    value = AgentCompanyApplyInput.model_construct(
        project_id=1,
        discovery_run_id=2,
        candidate_id=3,
        confirmed=False,  # type: ignore[arg-type]
    )
    with pytest.raises(AgentCompanyApplyConfirmationRequiredError, match="requires --yes"):
        service.apply(value)
    assert staging.events == []


def test_service_rejects_wrong_input_type_before_dependencies() -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    with pytest.raises(AgentCompanyApplyInvalidDataError):
        service.apply(object())  # type: ignore[arg-type]
    assert staging.events == []


@pytest.mark.parametrize(
    "run_status",
    [
        CompanyDiscoveryRunStatus.PENDING,
        CompanyDiscoveryRunStatus.NOT_FOUND,
        CompanyDiscoveryRunStatus.FAILED,
    ],
)
def test_service_rejects_ineligible_run(run_status: CompanyDiscoveryRunStatus) -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    staging.run.run_status = run_status
    with pytest.raises(AgentCompanyApplyNotEligibleError):
        service.apply(_input())
    assert "promote" not in staging.events


def test_service_rejects_stale_candidate() -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    staging.candidate.last_seen_run_id = 99
    with pytest.raises(AgentCompanyApplyStaleHandoffError):
        service.apply(_input())


def test_service_rejects_rejected_candidate() -> None:
    service, _ = _service(CompanyDiscoveryCandidateStatus.REJECTED)
    with pytest.raises(AgentCompanyApplyNotEligibleError):
        service.apply(_input())


@pytest.mark.parametrize("missing", ["run", "candidate", "company"])
def test_service_rejects_missing_records(missing: str) -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    if missing == "run":
        staging.run = None  # type: ignore[assignment]
    elif missing == "candidate":
        staging.candidate = None  # type: ignore[assignment]
    else:
        service.company_repository.company = None  # type: ignore[attr-defined]
    with pytest.raises(
        AgentCompanyApplyNotFoundError
        if missing in {"run", "candidate"}
        else AgentCompanyApplyConsistencyError
    ):
        service.apply(_input())


def test_service_rejects_string_enum_snapshot() -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    staging.run.run_status = "UNKNOWN"
    with pytest.raises(AgentCompanyApplyConsistencyError):
        service.apply(_input())


def test_service_translates_lock_integrity_error() -> None:
    service, _ = _service(CompanyDiscoveryCandidateStatus.REVIEWED)

    def fail(project_id: int) -> None:
        raise IntegrityError("statement", {}, Exception("conflict"))

    service.company_repository.acquire_promotion_scope = fail  # type: ignore[method-assign]
    with pytest.raises(AgentCompanyApplyConflictError):
        service.apply(_input())


def test_service_preserves_base_exception_identity() -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)
    sentinel = KeyboardInterrupt()

    def fail(run_id: int) -> Any:
        raise sentinel

    staging.get_run = fail  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt) as caught:
        service.apply(_input())
    assert caught.value is sentinel


def test_service_translates_dependency_exception_without_leaking_text() -> None:
    service, staging = _service(CompanyDiscoveryCandidateStatus.REVIEWED)

    def fail(run_id: int) -> Any:
        raise RuntimeError("secret")

    staging.get_run = fail  # type: ignore[method-assign]
    with pytest.raises(AgentCompanyApplyPersistenceError) as caught:
        service.apply(_input())
    assert "secret" not in str(caught.value)

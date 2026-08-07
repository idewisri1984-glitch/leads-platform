from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.modules.agent import (
    AgentContactApplyConfirmationRequiredError,
    AgentContactApplyConflictError,
    AgentContactApplyInput,
    AgentContactApplyInvalidDataError,
    AgentContactApplyNotEligibleError,
    AgentContactApplyResult,
    AgentContactApplyService,
    AgentContactApplyStaleHandoffError,
)
from app.modules.agent.contact_plan_contract import build_contact_plan_proposals
from app.modules.agent.contact_plan_handoff import build_agent_contact_plan_handoff_token
from app.modules.contact_discovery.candidate_promotion_schemas import (
    ContactDiscoveryCandidatePromotionResult,
)
from app.modules.contact_discovery.candidate_review_schemas import (
    ContactDiscoveryCandidateReviewResult,
)
from app.modules.contact_discovery.models import (
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateRead


def _candidate_read(status: ContactDiscoveryCandidateStatus) -> ContactDiscoveryCandidateRead:
    now = datetime(2026, 8, 7, tzinfo=UTC)
    return ContactDiscoveryCandidateRead(
        id=3,
        company_id=2,
        promoted_contact_id=None,
        name="Ada Lovelace",
        title="Founder",
        email="ada@example.com",
        normalized_email="ada@example.com",
        phone="+1 555 0100",
        source_url="https://example.com/team",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
        confidence=80,
        discovery_status=status,
        deduplication_key="email:ada@example.com",
        notes=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


class Dependencies:
    def __init__(self, status: ContactDiscoveryCandidateStatus) -> None:
        self.events: list[str] = []
        self.company = SimpleNamespace(id=2, project_id=1, name="Acme", website="https://acme.test")
        self.state = SimpleNamespace(
            company_id=2,
            provider="website",
            discovery_status=ContactDiscoveryStatus.SUCCEEDED,
            checked_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
            last_error=None,
        )
        self.candidate = SimpleNamespace(**_candidate_read(status).model_dump())
        self.candidate.promoted_contact_id = (
            10 if status is ContactDiscoveryCandidateStatus.PROMOTED else None
        )
        self.contact = SimpleNamespace(id=10, company_id=2)
        self.leads: list[Any] = []
        self.tasks: list[Any] = []

    def acquire_promotion_scope(self, company_id: int) -> None:
        self.events.append("scope")

    def get_for_project(self, project_id: int, company_id: int) -> Any:
        self.events.append("company")
        return self.company

    def get_state_for_update(self, company_id: int) -> Any:
        self.events.append("state")
        return self.state

    def get_candidate_for_promotion(self, company_id: int, candidate_id: int) -> Any:
        self.events.append("candidate")
        return self.candidate

    def get_for_company(self, company_id: int, contact_id: int) -> Any:
        self.events.append("contact")
        return self.contact

    def mark_reviewed(self, company_id: int, candidate_id: int) -> Any:
        self.events.append("review")
        self.candidate.discovery_status = ContactDiscoveryCandidateStatus.REVIEWED
        return ContactDiscoveryCandidateReviewResult(
            candidate=_candidate_read(ContactDiscoveryCandidateStatus.REVIEWED),
            previous_status=ContactDiscoveryCandidateStatus.DISCOVERED,
            current_status=ContactDiscoveryCandidateStatus.REVIEWED,
            changed=True,
        )

    def promote(self, company_id: int, candidate_id: int) -> Any:
        self.events.append("promote")
        previous = self.candidate.discovery_status
        changed = previous is not ContactDiscoveryCandidateStatus.PROMOTED
        self.candidate.discovery_status = ContactDiscoveryCandidateStatus.PROMOTED
        self.candidate.promoted_contact_id = 10
        return ContactDiscoveryCandidatePromotionResult(
            candidate_id=3,
            company_id=2,
            contact_id=10,
            previous_status=previous,
            current_status=ContactDiscoveryCandidateStatus.PROMOTED,
            created_contact=changed,
            changed=changed,
        )

    def get_by_contact(self, contact_id: int) -> list[Any]:
        self.events.append("leads")
        return self.leads

    def create_for_contact(self, **values: Any) -> Any:
        self.events.append("lead-create")
        lead = SimpleNamespace(id=20, notes=None, **values)
        self.leads.append(lead)
        return lead

    def get_by_lead(self, lead_id: int) -> list[Any]:
        self.events.append("tasks")
        return self.tasks

    def create_for_lead(self, **values: Any) -> Any:
        self.events.append("task-create")
        task = SimpleNamespace(id=30, status="TODO", due_at=None, **values)
        self.tasks.append(task)
        return task


def _token(deps: Dependencies, *, goal: str = "Partner") -> str:
    proposals = build_contact_plan_proposals(
        company_name=deps.company.name,
        candidate_name=deps.candidate.name,
        candidate_title=deps.candidate.title,
        goal=goal,
    )
    return build_agent_contact_plan_handoff_token(
        project_id=1,
        company_id=2,
        company_name=deps.company.name,
        company_website=deps.company.website,
        goal=goal,
        provider_name=deps.state.provider,
        discovery_checked_at=deps.state.checked_at,
        candidate_id=3,
        candidate_deduplication_key=deps.candidate.deduplication_key,
        candidate_name=deps.candidate.name,
        candidate_title=deps.candidate.title,
        candidate_email=deps.candidate.email,
        candidate_phone=deps.candidate.phone,
        candidate_source_url=deps.candidate.source_url,
        candidate_source_type=deps.candidate.source_type,
        candidate_confidence=float(deps.candidate.confidence) / 100.0,
        proposed_lead_title=proposals.lead_title,
        proposed_task_title=proposals.task_title,
        proposed_task_description=proposals.task_description,
    )


def _input(deps: Dependencies, **changes: Any) -> AgentContactApplyInput:
    values = dict(
        project_id=1,
        company_id=2,
        candidate_id=3,
        goal="Partner",
        handoff_token=_token(deps),
        confirmed=True,
    )
    values.update(changes)
    return AgentContactApplyInput(**values)


def _service(deps: Dependencies) -> AgentContactApplyService:
    return AgentContactApplyService(
        company_repository=cast(Any, deps),
        contact_repository=cast(Any, deps),
        discovery_repository=cast(Any, deps),
        review_service=cast(Any, deps),
        promotion_service=cast(Any, deps),
        lead_repository=cast(Any, deps),
        task_repository=cast(Any, deps),
    )


@pytest.mark.parametrize("field", ["project_id", "company_id", "candidate_id"])
@pytest.mark.parametrize("value", [0, -1, True, "1"])
def test_input_rejects_invalid_ids(field: str, value: object) -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    values = _input(deps).model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        AgentContactApplyInput(**values)


@pytest.mark.parametrize("token", ["x", "A" * 64, "g" * 64, type("S", (str,), {})("a" * 64)])
def test_input_rejects_invalid_token(token: object) -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    with pytest.raises(ValidationError):
        _input(deps, handoff_token=token)


def test_input_and_result_are_frozen_and_extra_forbid() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    value = _input(deps)
    with pytest.raises(ValidationError):
        value.goal = "changed"
    with pytest.raises(ValidationError):
        AgentContactApplyInput(**(value.model_dump() | {"extra": 1}))


def test_confirmation_precedes_dependencies_and_construct_bypass() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    value = AgentContactApplyInput.model_construct(
        project_id=1,
        company_id=2,
        candidate_id=3,
        goal="Partner",
        handoff_token="a" * 64,
        confirmed=False,
    )
    with pytest.raises(AgentContactApplyConfirmationRequiredError, match="requires --yes"):
        _service(deps).apply(value)
    assert deps.events == []


def test_constructed_malformed_input_is_deeply_rejected() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    value = AgentContactApplyInput.model_construct(
        project_id=True,
        company_id=2,
        candidate_id=3,
        goal="Partner",
        handoff_token="a" * 64,
        confirmed=True,
    )
    with pytest.raises(AgentContactApplyInvalidDataError):
        _service(deps).apply(value)
    assert deps.events == []


def test_shared_proposals_preserve_exact_stage4a_semantics() -> None:
    result = build_contact_plan_proposals(
        company_name="Acme",
        candidate_name="Ada Lovelace",
        candidate_title="Founder",
        goal="Partner",
    )
    assert result.lead_title == "Bohemia Bali partnership — Acme"
    assert result.task_title == "Review and prepare outreach to Ada Lovelace"
    assert result.task_description == (
        "A human must verify this contact before any action. No outreach has been sent, "
        "and no Lead or Task has been created. Selected person: Ada Lovelace with title Founder. "
        "Company: Acme. Prepare a personalized Bohemia Bali partnership message. Goal: Partner"
    )


@pytest.mark.parametrize(
    ("status", "reviewed", "promoted"),
    [
        (ContactDiscoveryCandidateStatus.DISCOVERED, True, True),
        (ContactDiscoveryCandidateStatus.REVIEWED, False, True),
        (ContactDiscoveryCandidateStatus.PROMOTED, False, False),
    ],
)
def test_lifecycle_independence_and_materialization(
    status: ContactDiscoveryCandidateStatus, reviewed: bool, promoted: bool
) -> None:
    deps = Dependencies(status)
    result = _service(deps).apply(_input(deps))
    assert result.handoff_verified is True
    assert (result.candidate_reviewed, result.candidate_promoted) == (reviewed, promoted)
    assert (result.contact_id, result.lead_id, result.task_id) == (10, 20, 30)
    assert deps.events[:4] == ["scope", "company", "state", "candidate"]
    assert deps.events.count("promote") == 1


def test_repeat_apply_reuses_exact_materialization() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.DISCOVERED)
    service = _service(deps)
    first = service.apply(_input(deps))
    second = service.apply(_input(deps))
    assert (first.contact_id, first.lead_id, first.task_id) == (
        second.contact_id,
        second.lead_id,
        second.task_id,
    )
    assert (second.contact_created, second.lead_created, second.task_created) == (
        False,
        False,
        False,
    )
    assert second.staging_mutated is second.crm_mutated is False


@pytest.mark.parametrize(
    "field",
    ["checked_at", "name", "title", "email", "phone", "source_url", "source_type", "confidence"],
)
def test_authoritative_changes_make_token_stale_before_mutation(field: str) -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.DISCOVERED)
    value = _input(deps)
    if field == "checked_at":
        deps.state.checked_at = datetime(2026, 8, 7, 13, tzinfo=UTC)
    elif field == "source_type":
        deps.candidate.source_type = ContactDiscoverySourceType.ABOUT_PAGE
    elif field == "confidence":
        deps.candidate.confidence = 81
    else:
        setattr(deps.candidate, field, f"changed-{field}")
    with pytest.raises(AgentContactApplyStaleHandoffError, match="handoff is stale"):
        _service(deps).apply(value)
    assert "review" not in deps.events and "promote" not in deps.events
    assert "lead-create" not in deps.events and "task-create" not in deps.events


def test_goal_and_company_semantic_changes_make_token_stale() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    value = _input(deps)
    deps.company.name = "Changed"
    with pytest.raises(AgentContactApplyStaleHandoffError):
        _service(deps).apply(value)
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    with pytest.raises(AgentContactApplyStaleHandoffError):
        _service(deps).apply(_input(deps, goal="Different"))


def test_rejected_candidate_is_not_eligible_after_verified_token() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REJECTED)
    with pytest.raises(AgentContactApplyNotEligibleError):
        _service(deps).apply(_input(deps))


def test_multiple_or_mismatched_lead_is_conflict() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.PROMOTED)
    deps.leads = [
        SimpleNamespace(id=20, company_id=2, contact_id=10, status="NEW", source=None, notes=None)
    ] * 2
    with pytest.raises(AgentContactApplyConflictError):
        _service(deps).apply(_input(deps))
    deps = Dependencies(ContactDiscoveryCandidateStatus.PROMOTED)
    deps.leads = [
        SimpleNamespace(id=20, company_id=2, contact_id=10, status="OLD", source=None, notes=None)
    ]
    with pytest.raises(AgentContactApplyConflictError):
        _service(deps).apply(_input(deps))


def test_multiple_or_mismatched_task_is_conflict() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.PROMOTED)
    deps.leads = [
        SimpleNamespace(id=20, company_id=2, contact_id=10, status="NEW", source=None, notes=None)
    ]
    deps.tasks = [
        SimpleNamespace(id=30, lead_id=20, title="x", description="x", status="TODO", due_at=None)
    ]
    with pytest.raises(AgentContactApplyConflictError):
        _service(deps).apply(_input(deps))


class ControlledBaseException(BaseException):
    pass


def test_base_exception_identity_is_preserved() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    sentinel = ControlledBaseException()
    deps.acquire_promotion_scope = cast(Any, lambda company_id: (_ for _ in ()).throw(sentinel))
    with pytest.raises(ControlledBaseException) as caught:
        _service(deps).apply(_input(deps))
    assert caught.value is sentinel


def test_result_contract_rejects_inconsistent_counts() -> None:
    deps = Dependencies(ContactDiscoveryCandidateStatus.REVIEWED)
    result = _service(deps).apply(_input(deps))
    with pytest.raises(ValidationError):
        AgentContactApplyResult.model_validate(result.model_dump() | {"network_call_count": 1})

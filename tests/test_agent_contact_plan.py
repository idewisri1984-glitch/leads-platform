from dataclasses import replace
from datetime import UTC, datetime
from math import inf, nan
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.modules.agent.contact_plan import (
    AgentContactPlanBindingMismatchError,
    AgentContactPlanCompanyNotFoundError,
    AgentContactPlanDiscoveryResultError,
    AgentContactPlanProjectNotFoundError,
    AgentContactPlanProviderError,
    AgentContactPlanService,
    AgentContactPlanWebsiteMissingError,
)
from app.modules.agent.contact_plan_schemas import (
    AgentContactDecision,
    AgentContactDiscoveryStatus,
    AgentContactPlanInput,
    AgentContactPlanResult,
)
from app.modules.contact_discovery.models import (
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
    clean_discovered_text,
    normalize_discovered_email,
    normalize_source_for_deduplication,
)
from app.modules.contact_discovery.repository import ContactDiscoveryCandidateUpsertResult
from app.modules.contact_discovery.schemas import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateRead,
)
from app.modules.contact_discovery.service import (
    ContactDiscoveryPersistedCandidate,
    ContactDiscoveryRunResult,
)
from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProviderResult


def candidate_create(company_id: int, **changes: object) -> ContactDiscoveryCandidateCreate:
    values: dict[str, object] = {
        "company_id": company_id,
        "name": "Ada Lovelace",
        "title": "Procurement Director",
        "email": "ada@example.com",
        "phone": None,
        "source_url": "https://example.com/team",
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 80,
    }
    return ContactDiscoveryCandidateCreate(**(values | changes))


class Provider:
    provider_name = "website"

    def __init__(self, candidates: tuple[ContactDiscoveryCandidateCreate, ...] = ()) -> None:
        self.candidates = candidates
        self.calls = 0
        self.closed = 0
        self.error: BaseException | None = None

    def discover(
        self, *, company_id: int, website_url: str
    ) -> WebsiteContactDiscoveryProviderResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return WebsiteContactDiscoveryProviderResult(
            candidates=self.candidates,
            attempted_pages=1,
            successful_pages=1,
        )

    def close(self) -> None:
        self.closed += 1


class Repository:
    def __init__(self) -> None:
        self.rows: dict[str, ContactDiscoveryCandidateRead] = {}
        self.next_id = 1
        self.states = 0
        self.protected: ContactDiscoveryCandidateStatus | None = None

    def upsert_candidate(
        self, company_id: int, value: ContactDiscoveryCandidateCreate
    ) -> ContactDiscoveryCandidateUpsertResult:
        source_url = clean_discovered_text(value.source_url)
        deduplication_source = (
            source_url if normalize_source_for_deduplication(source_url) is not None else None
        )
        key = build_contact_candidate_deduplication_key(
            email=normalize_discovered_email(value.email),
            name=clean_discovered_text(value.name),
            title=clean_discovered_text(value.title),
            source_url=deduplication_source,
        )
        existing = self.rows.get(key)
        if existing is None:
            existing = ContactDiscoveryCandidateRead(
                id=self.next_id,
                company_id=company_id,
                promoted_contact_id=(
                    99 if self.protected is ContactDiscoveryCandidateStatus.PROMOTED else None
                ),
                name=clean_discovered_text(value.name),
                title=clean_discovered_text(value.title),
                email=clean_discovered_text(value.email),
                normalized_email=normalize_discovered_email(value.email),
                phone=clean_discovered_text(value.phone),
                source_url=clean_discovered_text(value.source_url),
                source_type=value.source_type,
                confidence=value.confidence,
                discovery_status=self.protected or ContactDiscoveryCandidateStatus.DISCOVERED,
                deduplication_key=key,
                notes=None,
                last_error=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.rows[key] = existing
            self.next_id += 1
            return ContactDiscoveryCandidateUpsertResult(candidate=existing, created=True)
        if existing.discovery_status is not ContactDiscoveryCandidateStatus.DISCOVERED:
            return ContactDiscoveryCandidateUpsertResult(candidate=existing, protected=True)
        incoming = {
            "name": clean_discovered_text(value.name),
            "title": clean_discovered_text(value.title),
            "email": clean_discovered_text(value.email),
            "normalized_email": normalize_discovered_email(value.email),
            "phone": clean_discovered_text(value.phone),
            "source_url": clean_discovered_text(value.source_url),
            "notes": clean_discovered_text(value.notes),
            "last_error": clean_discovered_text(value.last_error),
        }
        updates = {
            field: incoming_value
            for field, incoming_value in incoming.items()
            if getattr(existing, field) is None and incoming_value is not None
        }
        if value.confidence > existing.confidence:
            updates["confidence"] = value.confidence
        if updates:
            existing = existing.model_copy(update=updates | {"updated_at": datetime.now(UTC)})
            self.rows[key] = existing
        return ContactDiscoveryCandidateUpsertResult(
            candidate=existing,
            updated=bool(updates),
        )

    def update_state(self, *args: object, **kwargs: object) -> object:
        self.states += 1
        return object()


def service(
    provider: Provider,
    repository: Repository | None = None,
    *,
    project: object | None = SimpleNamespace(id=1),
    company: object | None = SimpleNamespace(
        id=2, project_id=1, name="Meyer Davis", website="https://example.com"
    ),
) -> AgentContactPlanService:
    return AgentContactPlanService(
        projects=SimpleNamespace(get=lambda _id: project),
        companies=SimpleNamespace(get=lambda _id: company),
        discovery_repository=repository or Repository(),
        provider_factory=lambda: provider,
    )


def plan(provider: Provider, repository: Repository | None = None) -> AgentContactPlanResult:
    return service(provider, repository).plan(
        AgentContactPlanInput(project_id=1, company_id=2, goal="Find a partner")
    )


def persisted_candidate(**changes: object) -> ContactDiscoveryPersistedCandidate:
    values: dict[str, object] = {
        "id": 1,
        "company_id": 2,
        "promoted_contact_id": None,
        "name": "Ada Lovelace",
        "title": "Procurement Director",
        "email": "ada@example.com",
        "phone": None,
        "source_url": "https://example.com/team",
        "source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "confidence": 0.8,
        "discovery_status": ContactDiscoveryCandidateStatus.DISCOVERED,
        "deduplication_key": "email:ada@example.com",
    }
    return ContactDiscoveryPersistedCandidate(**(values | changes))  # type: ignore[arg-type]


def persisted_run(
    snapshot: ContactDiscoveryPersistedCandidate,
) -> ContactDiscoveryRunResult:
    return ContactDiscoveryRunResult(
        company_id=2,
        dry_run=False,
        status=ContactDiscoveryStatus.SUCCEEDED,
        candidates=(candidate_create(2),),
        attempted_pages=1,
        successful_pages=1,
        candidate_upserts=1,
        state_persisted=True,
        persisted_candidates=(snapshot,),
    )


@pytest.mark.parametrize("field", ["project_id", "company_id"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1"])
def test_input_requires_strict_positive_ids(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AgentContactPlanInput(
            **({"project_id": 1, "company_id": 2, "goal": "Goal"} | {field: value})
        )


def test_input_normalizes_goal_and_rejects_invalid_text() -> None:
    assert (
        AgentContactPlanInput(project_id=1, company_id=2, goal="  café\n goal ").goal == "café goal"
    )
    for value in (" ", "x\x00y", "x" * 2001):
        with pytest.raises(ValidationError):
            AgentContactPlanInput(project_id=1, company_id=2, goal=value)
    with pytest.raises(ValidationError):
        AgentContactPlanInput.model_validate(
            {"project_id": 1, "company_id": 2, "goal": "x", "extra": 1}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"provider_call_count": 0},
        {"successful_pages": 2},
        {"eligible_candidate_count": 1},
        {"human_review_required": False},
        {"contact_mutation_count": 1},
        {"decision": AgentContactDecision.SELECT},
    ],
)
def test_result_rejects_inconsistent_states(change: dict[str, object]) -> None:
    values = plan(Provider()).model_dump()
    with pytest.raises(ValidationError):
        AgentContactPlanResult(**(values | change))


def test_result_accepts_partial_with_or_without_eligible_candidate() -> None:
    selected = plan(Provider((candidate_create(2),)))
    assert (
        AgentContactPlanResult.model_validate(
            selected.model_dump() | {"discovery_status": AgentContactDiscoveryStatus.PARTIAL}
        ).decision
        is AgentContactDecision.SELECT
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"company_id": True},
        {"company_id": 3},
        {"confidence": True},
        {"confidence": 1},
        {"confidence": "0.9"},
        {"confidence": nan},
        {"confidence": inf},
        {"confidence": -inf},
        {"discovery_status": "UNKNOWN"},
        {"source_type": "UNKNOWN"},
        {"name": 1},
    ],
)
def test_persisted_candidate_boundary_rejects_invalid_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(AgentContactPlanDiscoveryResultError):
        AgentContactPlanService._validate_discovery(
            persisted_run(replace(persisted_candidate(), **changes)), 2
        )


def test_persisted_candidate_boundary_accepts_exact_persisted_enum_strings() -> None:
    snapshot = replace(
        persisted_candidate(), discovery_status="DISCOVERED", source_type="TEAM_PAGE"
    )
    result = AgentContactPlanService._validate_discovery(persisted_run(snapshot), 2)
    assert result.persisted_candidates == (snapshot,)

    empty = plan(Provider())
    assert (
        AgentContactPlanResult.model_validate(
            empty.model_dump() | {"discovery_status": AgentContactDiscoveryStatus.PARTIAL}
        ).decision
        is AgentContactDecision.NO_SELECTION
    )


def test_lookup_and_binding_errors_precede_provider_construction() -> None:
    provider = Provider()
    with pytest.raises(AgentContactPlanProjectNotFoundError):
        service(provider, project=None).plan(
            AgentContactPlanInput(project_id=1, company_id=2, goal="x")
        )
    with pytest.raises(AgentContactPlanCompanyNotFoundError):
        service(provider, company=None).plan(
            AgentContactPlanInput(project_id=1, company_id=2, goal="x")
        )
    with pytest.raises(AgentContactPlanBindingMismatchError):
        service(
            provider,
            company=SimpleNamespace(
                id=2, project_id=9, name="Other", website="https://example.com"
            ),
        ).plan(AgentContactPlanInput(project_id=1, company_id=2, goal="x"))
    with pytest.raises(AgentContactPlanWebsiteMissingError):
        service(
            provider, company=SimpleNamespace(id=2, project_id=1, name="Other", website=" ")
        ).plan(AgentContactPlanInput(project_id=1, company_id=2, goal="x"))
    assert provider.calls == 0


def test_provider_factory_and_execution_failures_are_sanitized() -> None:
    broken = service(Provider())
    broken.provider_factory = lambda: (_ for _ in ()).throw(RuntimeError("secret"))
    with pytest.raises(
        AgentContactPlanProviderError, match=r"^Contact discovery provider failed\.$"
    ):
        broken.plan(AgentContactPlanInput(project_id=1, company_id=2, goal="x"))
    provider = Provider()
    provider.error = RuntimeError("secret HTML")
    with pytest.raises(
        AgentContactPlanProviderError, match=r"^Contact discovery provider failed\.$"
    ):
        plan(provider)
    assert provider.calls == provider.closed == 1


def test_invalid_discovery_result_is_rejected() -> None:
    provider = Provider()
    boundary = service(provider)
    boundary.discovery_service_factory = lambda repository, value: SimpleNamespace(
        run=lambda **kwargs: object()
    )
    with pytest.raises(AgentContactPlanDiscoveryResultError):
        boundary.plan(AgentContactPlanInput(project_id=1, company_id=2, goal="x"))


def test_not_found_is_no_selection_and_partial_can_select() -> None:
    empty = plan(Provider())
    assert empty.decision is AgentContactDecision.NO_SELECTION
    assert empty.discovery_status is AgentContactDiscoveryStatus.NOT_FOUND
    candidate = candidate_create(2)
    provider = Provider((candidate,))
    provider.discover = lambda **kwargs: WebsiteContactDiscoveryProviderResult(
        candidates=(candidate,),
        attempted_pages=2,
        successful_pages=1,
        errors=("secondary_page_fetch_failed",),
    )
    selected = plan(provider)
    assert selected.decision is AgentContactDecision.SELECT
    assert selected.discovery_status is AgentContactDiscoveryStatus.PARTIAL


def test_only_current_run_is_ranked_and_protected_or_blank_names_are_ineligible() -> None:
    repository = Repository()
    historical = candidate_create(2, name="Historical", email="best@example.com")
    repository.upsert_candidate(2, historical)
    current = candidate_create(2, name="Current", title="Owner", email=None)
    result = plan(Provider((current,)), repository)
    assert result.selected_contact_name == "Current"
    repository = Repository()
    repository.protected = ContactDiscoveryCandidateStatus.REVIEWED
    assert plan(Provider((current,)), repository).decision is AgentContactDecision.NO_SELECTION
    blank = candidate_create(2, name=" ", title="Owner")
    assert plan(Provider((blank,))).decision is AgentContactDecision.NO_SELECTION


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        ("Procurement Manager", "Founder"),
        ("Founder", "Creative Director"),
        ("Creative Director", "Project Director"),
        ("Project Director", "Coordinator"),
    ],
)
def test_every_role_priority_group_is_ordered(better: str, worse: str) -> None:
    candidates = (
        candidate_create(2, name="Worse", title=worse, email="w@example.com"),
        candidate_create(2, name="Better", title=better, email=None),
    )
    assert plan(Provider(candidates)).selected_contact_name == "Better"


@pytest.mark.parametrize(
    ("title", "priority"),
    [
        ("Procurement Director", 0),
        ("Business Development Manager", 0),
        ("Vendor Relations", 0),
        ("Founder and Creative Director", 1),
        ("Co-Founder", 1),
        ("Co Founder", 1),
        ("Cofounder", 1),
        ("CEO", 1),
        ("Interior Design Director", 2),
        ("Senior Interior Designer", 3),
        ("Designer", 4),
    ],
)
def test_role_phrase_boundaries_accept_expected_titles(title: str, priority: int) -> None:
    assert AgentContactPlanService._role_priority(title) == priority


@pytest.mark.parametrize(
    "title",
    [
        "Vendorization Analyst",
        "Ownership Data Analyst",
        "Principalship Coordinator",
        "Presidential Archive Assistant",
        "Cofoundership Researcher",
        "Buyership Metrics Analyst",
    ],
)
def test_role_phrase_boundaries_reject_unrelated_substrings(title: str) -> None:
    assert AgentContactPlanService._role_priority(title) == 4


def test_email_confidence_and_id_are_deterministic_tiebreakers() -> None:
    email = candidate_create(2, name="Email", title="Owner", email="e@example.com", confidence=10)
    no_email = candidate_create(2, name="No email", title="Owner", email=None, confidence=100)
    assert plan(Provider((no_email, email))).selected_contact_name == "Email"
    low = candidate_create(2, name="Low", title="Owner", email=None, confidence=20)
    high = candidate_create(2, name="High", title="Owner", email=None, confidence=90)
    assert plan(Provider((low, high))).selected_contact_name == "High"
    first = candidate_create(2, name="First", title="Owner", email=None, confidence=50)
    second = candidate_create(2, name="Second", title="Owner", email=None, confidence=50)
    repository = Repository()
    one = plan(Provider((first, second)), repository)
    two = plan(Provider((first, second)), repository)
    assert one.selected_contact_name == two.selected_contact_name == "First"
    assert len(repository.rows) == 2


def test_fake_repository_matches_fill_empty_and_confidence_update_semantics() -> None:
    repository = Repository()
    first = candidate_create(
        2, name=None, title="Owner", email=" SAME@Example.com ", phone=None, confidence=40
    )
    created = repository.upsert_candidate(2, first).candidate
    second = candidate_create(
        2,
        name="Filled Name",
        title="Different Title",
        email="same@example.com",
        phone="+1 555 0100",
        confidence=90,
    )
    updated = repository.upsert_candidate(2, second).candidate
    assert updated.id == created.id
    assert updated.name == "Filled Name" and updated.phone == "+1 555 0100"
    assert updated.title == "Owner" and updated.confidence == 90
    lower = repository.upsert_candidate(
        2, candidate_create(2, name="Overwrite", email="same@example.com", confidence=10)
    ).candidate
    assert lower.name == "Filled Name" and lower.title == "Owner" and lower.confidence == 90


def test_duplicate_current_run_evidence_counts_unique_persisted_candidates() -> None:
    repository = Repository()
    first = candidate_create(2, name="First", email="same@example.com", confidence=40)
    second = candidate_create(2, name="Second", email="SAME@example.com", confidence=90)
    initial = plan(Provider((first, second)), repository)
    repeated = plan(Provider((first, second)), repository)
    assert len(repository.rows) == 1
    assert initial.selected_candidate_id == repeated.selected_candidate_id == 1
    assert initial.candidate_upsert_count == 2
    assert initial.staged_candidate_count == initial.eligible_candidate_count == 1


@pytest.mark.parametrize(
    "status",
    [
        ContactDiscoveryCandidateStatus.REVIEWED,
        ContactDiscoveryCandidateStatus.REJECTED,
        ContactDiscoveryCandidateStatus.PROMOTED,
    ],
)
def test_every_protected_status_is_bound_unchanged_and_ineligible(
    status: ContactDiscoveryCandidateStatus,
) -> None:
    repository = Repository()
    repository.protected = status
    evidence = candidate_create(2, email="protected@example.com")
    before = repository.upsert_candidate(2, evidence).candidate
    result = plan(Provider((evidence,)), repository)
    after = next(iter(repository.rows.values()))
    assert after == before
    assert after.discovery_status is status
    assert after.promoted_contact_id == (
        99 if status is ContactDiscoveryCandidateStatus.PROMOTED else None
    )
    assert result.decision is AgentContactDecision.NO_SELECTION
    assert result.eligible_candidate_count == 0 and result.staged_candidate_count == 1


def test_proposed_text_is_bounded_truthful_and_no_status_changes_occur() -> None:
    repository = Repository()
    result = service(
        Provider((candidate_create(2, name="Zoë", title="Business Development"),)),
        repository,
        company=SimpleNamespace(id=2, project_id=1, name="C" * 255, website="https://example.com"),
    ).plan(AgentContactPlanInput(project_id=1, company_id=2, goal="g" * 2000))
    assert len(result.proposed_lead_title or "") <= 255
    assert len(result.proposed_task_title or "") <= 255
    assert len(result.proposed_task_description or "") <= 4000
    text = result.proposed_task_description or ""
    assert "human must verify" in text and "No outreach has been sent" in text
    assert "no Lead or Task has been created" in text
    assert (
        result.contact_mutation_count
        == result.lead_mutation_count
        == result.task_mutation_count
        == 0
    )
    assert all(
        row.discovery_status is ContactDiscoveryCandidateStatus.DISCOVERED
        for row in repository.rows.values()
    )


@pytest.mark.parametrize("goal", ["g" * 2000, "界" * 2000])
def test_proposed_task_description_preserves_complete_maximum_goal(goal: str) -> None:
    result = service(Provider((candidate_create(2),))).plan(
        AgentContactPlanInput(project_id=1, company_id=2, goal=goal)
    )
    assert f"Goal: {goal}" in (result.proposed_task_description or "")
    assert len(result.proposed_task_description or "") <= 4000

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from inspect import signature
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
from app.modules.agent.contact_plan_handoff import (
    build_agent_contact_plan_handoff_token,
    canonicalize_handoff_datetime,
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
    ContactDiscoveryPersistedCandidateRaw,
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
        self.state: object | None = None
        self.checked_at_values: list[datetime] = []
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
            return self.result(existing, created=True)
        if existing.discovery_status is not ContactDiscoveryCandidateStatus.DISCOVERED:
            return self.result(existing, protected=True)
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
        return self.result(existing, updated=bool(updates))

    @staticmethod
    def result(
        candidate: ContactDiscoveryCandidateRead, **flags: bool
    ) -> ContactDiscoveryCandidateUpsertResult:
        return ContactDiscoveryCandidateUpsertResult(
            candidate=candidate,
            persisted_candidate=ContactDiscoveryPersistedCandidateRaw(
                id=candidate.id,
                company_id=candidate.company_id,
                promoted_contact_id=candidate.promoted_contact_id,
                name=candidate.name,
                title=candidate.title,
                email=candidate.email,
                normalized_email=candidate.normalized_email,
                phone=candidate.phone,
                source_url=candidate.source_url,
                source_type=candidate.source_type,
                confidence=float(candidate.confidence) / 100.0,
                discovery_status=candidate.discovery_status,
                deduplication_key=candidate.deduplication_key,
                notes=candidate.notes,
                last_error=candidate.last_error,
            ),
            **flags,
        )

    def update_state(self, *args: object, **kwargs: object) -> object:
        self.states += 1
        if self.checked_at_values:
            kwargs["checked_at"] = self.checked_at_values.pop(0)
        self.state = SimpleNamespace(company_id=args[0], **kwargs)
        return self.state

    def get_state_by_company_id(self, company_id: int) -> object | None:
        return self.state


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


def test_fake_repository_equal_confidence_does_not_report_update() -> None:
    repository = Repository()
    original = candidate_create(
        2,
        name="Original",
        email="equal-fake@example.com",
        confidence=73,
    )
    first = repository.upsert_candidate(2, original)
    second = repository.upsert_candidate(
        2,
        candidate_create(
            2,
            name="Replacement",
            email="equal-fake@example.com",
            confidence=73,
        ),
    )

    assert second.candidate.id == first.candidate.id
    assert second.candidate.confidence == 73
    assert second.candidate.name == "Original"
    assert second.updated is False


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


def handoff_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "project_id": 1,
        "company_id": 2,
        "company_name": "Meyer Davis",
        "company_website": "https://example.com",
        "goal": "Find a partner",
        "provider_name": "website",
        "discovery_checked_at": datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC),
        "candidate_id": 3,
        "candidate_deduplication_key": "email:will@example.com",
        "candidate_name": "Will Meyer",
        "candidate_title": "Founder",
        "candidate_email": "will@example.com",
        "candidate_phone": None,
        "candidate_source_url": "https://example.com/about",
        "candidate_source_type": ContactDiscoverySourceType.TEAM_PAGE,
        "candidate_confidence": 0.8,
        "proposed_lead_title": "Partnership with Meyer Davis",
        "proposed_task_title": "Review Will Meyer",
        "proposed_task_description": "Review before outreach.",
    }
    return values | changes


def test_handoff_builder_is_deterministic_and_canonicalizes_equivalent_datetimes() -> None:
    expected = build_agent_contact_plan_handoff_token(**handoff_values())
    assert expected == build_agent_contact_plan_handoff_token(**handoff_values())
    assert expected == build_agent_contact_plan_handoff_token(
        **handoff_values(discovery_checked_at=datetime(2026, 1, 2, 3, 4, 5, 6))
    )
    assert expected == build_agent_contact_plan_handoff_token(
        **handoff_values(
            discovery_checked_at=datetime(
                2026, 1, 2, 4, 4, 5, 6, tzinfo=timezone(timedelta(hours=1))
            )
        )
    )
    assert len(expected) == 64 and expected == expected.lower()


class DatetimeSubclass(datetime):
    pass


class RaisingTimezone(tzinfo):
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise self.error

    def dst(self, value: datetime | None) -> timedelta | None:
        return None


class InvalidOffsetTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        return timedelta(hours=24)

    def dst(self, value: datetime | None) -> timedelta | None:
        return None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 8, 6, 13, 46, 6, tzinfo=UTC), "2026-08-06T13:46:06.000000Z"),
        (datetime(2026, 8, 6, 13, 46, 6, 15939), "2026-08-06T13:46:06.015939Z"),
        (
            datetime(2026, 8, 7, 1, 30, tzinfo=timezone(timedelta(hours=2))),
            "2026-08-06T23:30:00.000000Z",
        ),
        (
            datetime(2026, 8, 6, 22, 30, tzinfo=timezone(-timedelta(hours=3))),
            "2026-08-07T01:30:00.000000Z",
        ),
        (
            datetime(2027, 1, 1, 1, 0, tzinfo=timezone(timedelta(hours=2))),
            "2026-12-31T23:00:00.000000Z",
        ),
    ],
)
def test_datetime_canonicalization_matrix(value: datetime, expected: str) -> None:
    assert canonicalize_handoff_datetime(value) == expected


def test_equivalent_datetime_instants_produce_equal_tokens() -> None:
    values = (
        datetime(2026, 8, 6, 13, 46, 6, 15939, tzinfo=UTC),
        datetime(2026, 8, 6, 13, 46, 6, 15939),
        datetime(2026, 8, 6, 15, 46, 6, 15939, tzinfo=timezone(timedelta(hours=2))),
        datetime(2026, 8, 6, 8, 46, 6, 15939, tzinfo=timezone(-timedelta(hours=5))),
    )
    canonical = {canonicalize_handoff_datetime(value) for value in values}
    tokens = {
        build_agent_contact_plan_handoff_token(**handoff_values(discovery_checked_at=value))
        for value in values
    }
    assert canonical == {"2026-08-06T13:46:06.015939Z"}
    assert len(tokens) == 1


@pytest.mark.parametrize(
    "value",
    [
        DatetimeSubclass(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=InvalidOffsetTimezone()),
        datetime(2026, 1, 1, tzinfo=RaisingTimezone(TypeError("controlled"))),
        datetime(2026, 1, 1, tzinfo=RaisingTimezone(RuntimeError("controlled"))),
    ],
)
def test_datetime_validation_rejects_invalid_values_with_value_error(value: datetime) -> None:
    with pytest.raises(ValueError):
        canonicalize_handoff_datetime(value)
    with pytest.raises(ValueError):
        build_agent_contact_plan_handoff_token(**handoff_values(discovery_checked_at=value))


class ControlledBaseException(BaseException):
    pass


@pytest.mark.parametrize(
    "error",
    [KeyboardInterrupt(), SystemExit(), GeneratorExit(), ControlledBaseException()],
)
def test_datetime_validation_preserves_base_exceptions(error: BaseException) -> None:
    value = datetime(2026, 1, 1, tzinfo=RaisingTimezone(error))
    with pytest.raises(type(error)) as captured:
        canonicalize_handoff_datetime(value)
    assert captured.value is error


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", 2),
        ("company_id", 3),
        ("company_name", "Other"),
        ("company_website", "https://other.example"),
        ("goal", "Other goal"),
        ("provider_name", "other"),
        ("discovery_checked_at", datetime(2026, 1, 2, 3, 4, 5, 7, tzinfo=UTC)),
        ("candidate_id", 4),
        ("candidate_deduplication_key", "name:will"),
        ("candidate_name", "Gray Davis"),
        ("candidate_title", "Principal"),
        ("candidate_email", None),
        ("candidate_phone", "+15550100"),
        ("candidate_source_url", "https://example.com/team"),
        ("candidate_source_type", ContactDiscoverySourceType.ABOUT_PAGE),
        ("candidate_confidence", 0.9),
        ("proposed_lead_title", "Other lead"),
        ("proposed_task_title", "Other task"),
        ("proposed_task_description", "Other description"),
    ],
)
def test_handoff_builder_changes_for_every_payload_input(field: str, value: object) -> None:
    baseline = build_agent_contact_plan_handoff_token(**handoff_values())
    assert build_agent_contact_plan_handoff_token(**handoff_values(**{field: value})) != baseline


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", True),
        ("candidate_id", 0),
        ("company_name", " "),
        ("provider_name", "x" * 101),
        ("discovery_checked_at", "2026-01-02"),
        ("candidate_source_type", "TEAM_PAGE"),
        ("candidate_confidence", 1),
        ("candidate_confidence", nan),
        ("candidate_deduplication_key", " "),
        ("candidate_email", 1),
    ],
)
def test_handoff_builder_rejects_invalid_boundary_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        build_agent_contact_plan_handoff_token(**handoff_values(**{field: value}))


@pytest.mark.parametrize(
    "token",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, b"a" * 64, 1, True, object()],
)
def test_result_rejects_invalid_handoff_tokens(token: object) -> None:
    values = plan(Provider((candidate_create(2),))).model_dump()
    with pytest.raises(ValidationError):
        AgentContactPlanResult(**(values | {"handoff_token": token}))


class StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "token",
    [
        None,
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        StringSubclass("a" * 64),
        b"a" * 64,
        1,
        True,
        object(),
    ],
)
@pytest.mark.parametrize("use_model_validate", [False, True])
def test_select_token_schema_adversarial_matrix(token: object, use_model_validate: bool) -> None:
    values = plan(Provider((candidate_create(2),))).model_dump()
    values["handoff_token"] = token
    with pytest.raises(ValidationError):
        if use_model_validate:
            AgentContactPlanResult.model_validate(values)
        else:
            AgentContactPlanResult(**values)


@pytest.mark.parametrize("use_model_validate", [False, True])
def test_valid_select_token_is_accepted(use_model_validate: bool) -> None:
    values = plan(Provider((candidate_create(2),))).model_dump()
    result = (
        AgentContactPlanResult.model_validate(values)
        if use_model_validate
        else AgentContactPlanResult(**values)
    )
    assert result.handoff_token == values["handoff_token"]


def test_result_model_remains_frozen_and_forbids_extra_fields() -> None:
    result = plan(Provider((candidate_create(2),)))
    with pytest.raises(ValidationError):
        result.handoff_token = "b" * 64
    with pytest.raises(ValidationError):
        AgentContactPlanResult.model_validate(result.model_dump() | {"extra": 1})


def test_no_selection_requires_null_handoff_token() -> None:
    values = plan(Provider()).model_dump()
    assert values["handoff_token"] is None
    with pytest.raises(ValidationError):
        AgentContactPlanResult(**(values | {"handoff_token": "a" * 64}))
    assert AgentContactPlanResult.model_validate(values).handoff_token is None


@pytest.mark.parametrize(
    "change",
    [
        {"state": None},
        {"company_id": True},
        {"company_id": "2"},
        {"company_id": 9},
        {"provider": "other"},
        {"provider": ""},
        {"provider": " website "},
        {"provider": StringSubclass("website")},
        {"provider": b"website"},
        {"provider": None},
        {"provider": object()},
        {"discovery_status": ContactDiscoveryStatus.PARTIAL},
        {"discovery_status": "succeeded"},
        {"discovery_status": " SUCCEEDED "},
        {"discovery_status": "UNKNOWN"},
        {"discovery_status": StringSubclass("SUCCEEDED")},
        {"discovery_status": b"SUCCEEDED"},
        {"discovery_status": True},
        {"discovery_status": 1},
        {"discovery_status": None},
        {"discovery_status": object()},
        {"checked_at": None},
        {"checked_at": "2026-08-06T13:46:06Z"},
        {"last_error": "other"},
        {"last_error": 1},
    ],
)
def test_plan_rejects_invalid_persisted_discovery_state(change: dict[str, object]) -> None:
    repository = Repository()
    provider = Provider((candidate_create(2),))
    if "state" in change:
        repository.update_state = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    else:
        original = repository.update_state

        def update(*args: object, **kwargs: object) -> object:
            state = original(*args, **kwargs)
            repository.state = SimpleNamespace(**(vars(state) | change))
            return repository.state

        repository.update_state = update  # type: ignore[method-assign]
    with pytest.raises(
        AgentContactPlanDiscoveryResultError,
        match=r"^Contact discovery result is invalid\.$",
    ):
        plan(provider, repository)


class EqualityCompatibleStatus:
    def __eq__(self, other: object) -> bool:
        return other == "SUCCEEDED"


def test_plan_rejects_equality_compatible_persisted_status() -> None:
    repository = Repository()
    original = repository.update_state

    def update(*args: object, **kwargs: object) -> object:
        state = original(*args, **kwargs)
        repository.state = SimpleNamespace(
            **(vars(state) | {"discovery_status": EqualityCompatibleStatus()})
        )
        return repository.state

    repository.update_state = update  # type: ignore[method-assign]
    with pytest.raises(AgentContactPlanDiscoveryResultError):
        plan(Provider((candidate_create(2),)), repository)


@pytest.mark.parametrize(
    "status",
    [ContactDiscoveryStatus.SUCCEEDED, "SUCCEEDED"],
)
def test_plan_accepts_exact_persisted_status_representations(status: object) -> None:
    repository = Repository()
    original = repository.update_state

    def update(*args: object, **kwargs: object) -> object:
        state = original(*args, **kwargs)
        repository.state = SimpleNamespace(**(vars(state) | {"discovery_status": status}))
        return repository.state

    repository.update_state = update  # type: ignore[method-assign]
    assert (
        plan(Provider((candidate_create(2),)), repository).decision is AgentContactDecision.SELECT
    )


@pytest.mark.parametrize("error", [TypeError("controlled"), RuntimeError("controlled")])
def test_service_maps_timezone_failures_to_safe_discovery_error(error: Exception) -> None:
    repository = Repository()
    original = repository.update_state

    def update(*args: object, **kwargs: object) -> object:
        state = original(*args, **kwargs)
        repository.state = SimpleNamespace(
            **(vars(state) | {"checked_at": datetime(2026, 1, 1, tzinfo=RaisingTimezone(error))})
        )
        return repository.state

    repository.update_state = update  # type: ignore[method-assign]
    with pytest.raises(
        AgentContactPlanDiscoveryResultError,
        match=r"^Contact discovery result is invalid\.$",
    ):
        plan(Provider((candidate_create(2),)), repository)


def test_current_run_checked_at_invalidates_same_candidate_handoff() -> None:
    repository = Repository()
    generation_a = datetime(2026, 8, 6, 13, 46, 6, 15939, tzinfo=UTC)
    generation_b = datetime(2026, 8, 6, 13, 47, 6, 15939, tzinfo=UTC)
    repository.checked_at_values = [generation_a, generation_b]
    provider = Provider((candidate_create(2),))
    first = plan(provider, repository)
    second = plan(provider, repository)
    assert first.selected_candidate_id == second.selected_candidate_id
    stable_fields = (
        "project_id",
        "company_id",
        "selected_contact_name",
        "selected_contact_title",
        "selected_contact_email",
        "selected_contact_phone",
        "selected_contact_source_url",
        "selected_contact_source_type",
        "selected_contact_confidence",
        "goal",
        "proposed_lead_title",
        "proposed_task_title",
        "proposed_task_description",
    )
    assert all(getattr(first, field) == getattr(second, field) for field in stable_fields)
    assert repository.states == provider.calls == 2
    assert repository.state is not None
    assert repository.state.checked_at == generation_b
    assert first.handoff_token != second.handoff_token


@pytest.mark.parametrize(
    "lifecycle",
    [
        ContactDiscoveryCandidateStatus.DISCOVERED,
        ContactDiscoveryCandidateStatus.REVIEWED,
        ContactDiscoveryCandidateStatus.PROMOTED,
    ],
)
def test_handoff_builder_is_lifecycle_independent(
    lifecycle: ContactDiscoveryCandidateStatus,
) -> None:
    assert lifecycle.value
    assert build_agent_contact_plan_handoff_token(
        **handoff_values()
    ) == build_agent_contact_plan_handoff_token(**handoff_values())
    parameters = signature(build_agent_contact_plan_handoff_token).parameters
    assert {
        "discovery_status",
        "promoted_contact_id",
        "created_at",
        "updated_at",
    }.isdisjoint(parameters)


def test_synthetic_meyer_davis_selection_has_handoff_token() -> None:
    result = plan(Provider((candidate_create(2, name="Will Meyer", title="Founder", email=None),)))
    assert result.selected_contact_name == "Will Meyer"
    assert result.handoff_token is not None and len(result.handoff_token) == 64

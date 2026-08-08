from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.core.database.base import Base
from app.modules.agent.contact_apply import (
    AgentContactApplyConflictError,
    AgentContactApplyConsistencyError,
    AgentContactApplyService,
    AgentContactApplyStaleHandoffError,
)
from app.modules.agent.contact_apply_schemas import AgentContactApplyInput
from app.modules.agent.contact_plan_contract import (
    build_contact_plan_proposals,
    build_legacy_contact_plan_task_description,
)
from app.modules.agent.contact_plan_handoff import build_agent_contact_plan_handoff_token
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.contact_discovery.candidate_promotion import (
    ContactDiscoveryCandidatePromotionService,
)
from app.modules.contact_discovery.candidate_review import ContactDiscoveryCandidateReviewService
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.models import Project
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stage4b.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        project = Project(name="Stage 4B")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="Acme", website="https://acme.test")
        session.add(company)
        session.flush()
        state = CompanyContactDiscoveryState(
            company_id=company.id,
            provider="website",
            discovery_status=ContactDiscoveryStatus.SUCCEEDED,
            checked_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
            last_error=None,
        )
        candidate = ContactDiscoveryCandidate(
            company_id=company.id,
            name="Ada Lovelace",
            title="Founder",
            email="ada@example.com",
            normalized_email="ada@example.com",
            phone="+1 555 0100",
            source_url="https://acme.test/team",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
            confidence=80,
            discovery_status=ContactDiscoveryCandidateStatus.DISCOVERED,
            deduplication_key="email:ada@example.com",
        )
        session.add_all((state, candidate))
        session.commit()
        ids = project.id, company.id, candidate.id
    yield engine, factory, ids
    engine.dispose()


def _service(session: Session) -> AgentContactApplyService:
    discovery = ContactDiscoveryRepository(session)
    contacts = ContactRepository(session)
    return AgentContactApplyService(
        company_repository=CompanyRepository(session),
        contact_repository=contacts,
        discovery_repository=discovery,
        review_service=ContactDiscoveryCandidateReviewService(discovery),
        promotion_service=ContactDiscoveryCandidatePromotionService(discovery, contacts),
        lead_repository=LeadRepository(session),
        task_repository=TaskRepository(session),
    )


class _InvertedPromotionResult:
    def __init__(self, service: ContactDiscoveryCandidatePromotionService) -> None:
        self.service = service

    def promote(self, company_id: int, candidate_id: int):
        result = self.service.promote(company_id, candidate_id)
        return type(result)(
            **(result.model_dump() | {"created_contact": not result.created_contact})
        )


def _hostile_service(session: Session) -> AgentContactApplyService:
    discovery = ContactDiscoveryRepository(session)
    contacts = ContactRepository(session)
    return AgentContactApplyService(
        company_repository=CompanyRepository(session),
        contact_repository=contacts,
        discovery_repository=discovery,
        review_service=ContactDiscoveryCandidateReviewService(discovery),
        promotion_service=_InvertedPromotionResult(
            ContactDiscoveryCandidatePromotionService(discovery, contacts)
        ),  # type: ignore[arg-type]
        lead_repository=LeadRepository(session),
        task_repository=TaskRepository(session),
    )


def _input(
    session: Session,
    ids: tuple[int, int, int],
    *,
    legacy_description: bool = False,
) -> AgentContactApplyInput:
    project_id, company_id, candidate_id = ids
    company = session.get(Company, company_id)
    state = session.scalar(
        select(CompanyContactDiscoveryState).where(
            CompanyContactDiscoveryState.company_id == company_id
        )
    )
    candidate = session.get(ContactDiscoveryCandidate, candidate_id)
    assert company is not None and state is not None and candidate is not None
    source_type = candidate.source_type
    if type(source_type) is str:
        source_type = ContactDiscoverySourceType(source_type)
    elif type(source_type) is not ContactDiscoverySourceType:
        raise AssertionError("Persisted candidate source type is invalid.")
    proposals = build_contact_plan_proposals(
        company_name=company.name,
        candidate_name=candidate.name,
        candidate_title=candidate.title,
        goal="Partner",
    )
    token = build_agent_contact_plan_handoff_token(
        project_id=project_id,
        company_id=company_id,
        company_name=company.name,
        company_website=company.website,
        goal="Partner",
        provider_name=state.provider,
        discovery_checked_at=state.checked_at,
        candidate_id=candidate_id,
        candidate_deduplication_key=candidate.deduplication_key,
        candidate_name=candidate.name,
        candidate_title=candidate.title,
        candidate_email=candidate.email,
        candidate_phone=candidate.phone,
        candidate_source_url=candidate.source_url,
        candidate_source_type=source_type,
        candidate_confidence=float(candidate.confidence) / 100.0,
        proposed_lead_title=proposals.lead_title,
        proposed_task_title=proposals.task_title,
        proposed_task_description=(
            build_legacy_contact_plan_task_description(
                company_name=company.name,
                candidate_name=candidate.name,
                candidate_title=candidate.title,
                goal="Partner",
            )
            if legacy_description
            else proposals.task_description
        ),
    )
    return AgentContactApplyInput(
        project_id=project_id,
        company_id=company_id,
        candidate_id=candidate_id,
        goal="Partner",
        handoff_token=token,
        confirmed=True,
    )


def test_fresh_apply_and_repeat_are_transactional_and_idempotent(database) -> None:
    engine, factory, ids = database
    commits = 0

    @event.listens_for(Session, "after_commit")
    def count_commit(session):
        nonlocal commits
        commits += 1

    try:
        with factory() as session:
            data = _input(session, ids)
            before = commits
            first = _service(session).apply(data)
            assert commits == before
            assert (first.contact_created, first.lead_created, first.task_created) == (
                True,
                True,
                True,
            )
            session.commit()
        with factory() as session:
            second = _service(session).apply(data)
            assert (second.contact_id, second.lead_id, second.task_id) == (
                first.contact_id,
                first.lead_id,
                first.task_id,
            )
            assert (second.contact_created, second.lead_created, second.task_created) == (
                False,
                False,
                False,
            )
            assert second.staging_mutated is second.crm_mutated is False
            session.commit()
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Contact)) == 1
            assert session.scalar(select(func.count()).select_from(Lead)) == 1
            assert session.scalar(select(func.count()).select_from(Task)) == 1
            task = session.scalar(select(Task))
            assert task is not None
            assert "review and prepare personalized outreach" in (task.description or "")
            assert "no Lead" not in (task.description or "")
            assert "no Task" not in (task.description or "")
    finally:
        event.remove(Session, "after_commit", count_commit)


def _seed_legacy_materialization(session: Session, ids: tuple[int, int, int]) -> tuple[int, str]:
    _, company_id, candidate_id = ids
    candidate = session.get(ContactDiscoveryCandidate, candidate_id)
    assert candidate is not None
    contact = ContactRepository(session).create_for_promotion(
        company_id=company_id,
        first_name="Ada",
        last_name="Lovelace",
        job_title="Founder",
        email="ada@example.com",
        phone="+1 555 0100",
        source="CONTACT_DISCOVERY",
        external_id="legacy-stage4e",
        status="NEW",
    )
    candidate.discovery_status = ContactDiscoveryCandidateStatus.PROMOTED.value
    candidate.promoted_contact_id = contact.id
    lead = LeadRepository(session).create_for_contact(
        company_id=company_id,
        contact_id=contact.id,
        status="NEW",
        source=None,
    )
    proposals = build_contact_plan_proposals(
        company_name="Acme",
        candidate_name="Ada Lovelace",
        candidate_title="Founder",
        goal="Partner",
    )
    legacy = build_legacy_contact_plan_task_description(
        company_name="Acme",
        candidate_name="Ada Lovelace",
        candidate_title="Founder",
        goal="Partner",
    )
    task = TaskRepository(session).create_for_lead(
        lead_id=lead.id,
        title=proposals.task_title,
        description=legacy,
    )
    session.commit()
    return task.id, legacy


def test_legacy_task_is_reused_and_normalized_without_duplicate(database) -> None:
    _, factory, ids = database
    with factory() as session:
        task_id, legacy = _seed_legacy_materialization(session, ids)
    with factory() as session:
        data = _input(session, ids)
        result = _service(session).apply(data)
        assert result.task_id == task_id
        assert (result.task_created, result.task_reused) == (False, True)
        assert (result.task_mutation_count, result.crm_mutated) == (1, True)
        session.commit()
    with factory() as session:
        tasks = list(session.scalars(select(Task)))
        assert len(tasks) == 1
        assert tasks[0].id == task_id
        assert tasks[0].description != legacy
        assert "no Lead" not in (tasks[0].description or "")
        assert "no Task" not in (tasks[0].description or "")


def test_pre_fix_description_token_is_stale_before_mutation(database) -> None:
    _, factory, ids = database
    with factory() as session:
        data = _input(session, ids, legacy_description=True)
        with pytest.raises(AgentContactApplyStaleHandoffError, match="handoff is stale"):
            _service(session).apply(data)
        session.rollback()
    with factory() as session:
        candidate = session.get(ContactDiscoveryCandidate, ids[2])
        assert candidate is not None
        assert candidate.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED.value
        assert candidate.promoted_contact_id is None
        assert session.scalar(select(func.count()).select_from(Contact)) == 0
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_legacy_normalization_rolls_back_after_late_validation_failure(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, factory, ids = database
    with factory() as session:
        task_id, legacy = _seed_legacy_materialization(session, ids)
    with factory() as session:
        service = _service(session)

        def fail_validation(*values: object) -> None:
            raise AgentContactApplyConsistencyError("Agent contact apply state is inconsistent.")

        monkeypatch.setattr(service, "_validate_task", fail_validation)
        with pytest.raises(AgentContactApplyConsistencyError):
            service.apply(_input(session, ids))
        session.rollback()
    with factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.description == legacy
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_ambiguous_legacy_and_current_tasks_are_rejected_without_normalization(database) -> None:
    _, factory, ids = database
    with factory() as session:
        task_id, legacy = _seed_legacy_materialization(session, ids)
        legacy_task = session.get(Task, task_id)
        assert legacy_task is not None
        proposals = build_contact_plan_proposals(
            company_name="Acme",
            candidate_name="Ada Lovelace",
            candidate_title="Founder",
            goal="Partner",
        )
        TaskRepository(session).create_for_lead(
            lead_id=legacy_task.lead_id,
            title=proposals.task_title,
            description=proposals.task_description,
        )
        session.commit()
    with factory() as session:
        with pytest.raises(AgentContactApplyConflictError, match="conflicting CRM state"):
            _service(session).apply(_input(session, ids))
        session.rollback()
    with factory() as session:
        tasks = list(session.scalars(select(Task).order_by(Task.id)))
        assert len(tasks) == 2
        assert tasks[0].description == legacy


def test_late_task_failure_rolls_back_all_service_mutations(database) -> None:
    engine, factory, ids = database
    with factory() as session:
        data = _input(session, ids)
        service = _service(session)

        def fail_task(**values):
            raise RuntimeError("controlled late task failure")

        service.task_repository.create_for_lead = fail_task  # type: ignore[method-assign]
        with pytest.raises(Exception, match="could not be persisted"):
            service.apply(data)
        session.rollback()
    with factory() as verification:
        candidate = verification.get(ContactDiscoveryCandidate, ids[2])
        assert candidate is not None
        assert type(candidate.discovery_status) is str
        assert candidate.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED.value
        assert candidate.promoted_contact_id is None
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_hostile_false_reused_result_rolls_back_real_contact_creation(database) -> None:
    engine, factory, ids = database
    with factory() as session:
        data = _input(session, ids)
        with pytest.raises(AgentContactApplyConsistencyError, match="state is inconsistent"):
            _hostile_service(session).apply(data)
        session.rollback()
    with factory() as verification:
        candidate = verification.get(ContactDiscoveryCandidate, ids[2])
        assert candidate is not None
        assert candidate.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED.value
        assert candidate.promoted_contact_id is None
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_hostile_false_created_result_preserves_preexisting_contact(database) -> None:
    engine, factory, ids = database
    with factory() as session:
        contact = ContactRepository(session).create_for_promotion(
            company_id=ids[1],
            first_name="Existing",
            last_name=None,
            job_title=None,
            email="ada@example.com",
            phone=None,
            source="CONTACT_DISCOVERY",
            external_id="preexisting",
            status="NEW",
        )
        contact_id = contact.id
        session.commit()
    with factory() as session:
        data = _input(session, ids)
        with pytest.raises(AgentContactApplyConsistencyError, match="state is inconsistent"):
            _hostile_service(session).apply(data)
        session.rollback()
    with factory() as verification:
        candidate = verification.get(ContactDiscoveryCandidate, ids[2])
        assert candidate is not None
        assert candidate.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED.value
        assert candidate.promoted_contact_id is None
        assert verification.get(Contact, contact_id) is not None
        assert verification.scalar(select(func.count()).select_from(Contact)) == 1
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_state_lock_query_uses_populate_existing_and_for_update() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.statement = None

        def scalar(self, statement):
            self.statement = statement
            return None

    session = RecordingSession()
    ContactDiscoveryRepository(session).get_state_for_update(2)  # type: ignore[arg-type]
    assert session.statement is not None
    assert session.statement.get_execution_options()["populate_existing"] is True
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert "company_contact_discovery_states" in sql

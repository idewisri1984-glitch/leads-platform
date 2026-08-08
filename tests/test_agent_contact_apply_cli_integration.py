import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

import app.cli.agent as agent_cli
from app.cli.main import app
from app.core.database.base import Base
from app.modules.agent.contact_apply_schemas import AgentContactApplyResult
from app.modules.agent.contact_plan_contract import (
    build_contact_plan_proposals,
    build_legacy_contact_plan_task_description,
)
from app.modules.agent.contact_plan_handoff import build_agent_contact_plan_handoff_token
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository

runner = CliRunner()
GOAL = "Partner"


@pytest.fixture
def database(tmp_path: Path):
    path = tmp_path / "leads-platform-stage4c-contact-apply.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        project = Project(name="Stage 4C")
        foreign_project = Project(name="Foreign")
        session.add_all((project, foreign_project))
        session.flush()
        company = Company(project_id=project.id, name="Acme", website="https://acme.test")
        peer_company = Company(
            project_id=project.id,
            name="Peer",
            website="https://peer.test",
        )
        foreign_company = Company(
            project_id=foreign_project.id,
            name="Other",
            website="https://other.test",
        )
        session.add_all((company, peer_company, foreign_company))
        session.flush()
        state = CompanyContactDiscoveryState(
            company_id=company.id,
            provider="website",
            discovery_status=ContactDiscoveryStatus.SUCCEEDED,
            checked_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
            last_error=None,
        )
        foreign_state = CompanyContactDiscoveryState(
            company_id=foreign_company.id,
            provider="website",
            discovery_status=ContactDiscoveryStatus.SUCCEEDED,
            checked_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
            last_error=None,
        )
        candidate = _candidate(company.id, "ada@example.com")
        peer_candidate = _candidate(peer_company.id, "katherine@example.com")
        foreign_candidate = _candidate(foreign_company.id, "grace@example.com")
        session.add_all((state, foreign_state, candidate, peer_candidate, foreign_candidate))
        session.commit()
        ids = {
            "project": project.id,
            "company": company.id,
            "candidate": candidate.id,
            "peer_candidate": peer_candidate.id,
            "foreign_project": foreign_project.id,
            "foreign_company": foreign_company.id,
            "foreign_candidate": foreign_candidate.id,
        }
    yield factory, ids
    engine.dispose()


def _candidate(company_id: int, email: str) -> ContactDiscoveryCandidate:
    return ContactDiscoveryCandidate(
        company_id=company_id,
        name="Ada Lovelace",
        title="Founder",
        email=email,
        normalized_email=email,
        phone="+1 555 0100",
        source_url="https://acme.test/team",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
        confidence=80,
        discovery_status=ContactDiscoveryCandidateStatus.DISCOVERED,
        deduplication_key=f"email:{email}",
    )


def _token(factory: sessionmaker[Session], ids: dict[str, int], goal: str = GOAL) -> str:
    with factory() as session:
        company = session.get(Company, ids["company"])
        candidate = session.get(ContactDiscoveryCandidate, ids["candidate"])
        state = session.get(CompanyContactDiscoveryState, ids["company"])
        assert company is not None and candidate is not None and state is not None
        source_type = ContactDiscoverySourceType(candidate.source_type)
        proposals = build_contact_plan_proposals(
            company_name=company.name,
            candidate_name=candidate.name,
            candidate_title=candidate.title,
            goal=goal,
        )
        return build_agent_contact_plan_handoff_token(
            project_id=ids["project"],
            company_id=company.id,
            company_name=company.name,
            company_website=company.website,
            goal=goal,
            provider_name=state.provider,
            discovery_checked_at=state.checked_at,
            candidate_id=candidate.id,
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
            proposed_task_description=proposals.task_description,
        )


def _arguments(ids: dict[str, int], token: str, *, goal: str = GOAL) -> list[str]:
    return [
        "agent",
        "contact-select",
        "apply",
        "--project-id",
        str(ids["project"]),
        "--company-id",
        str(ids["company"]),
        "--candidate-id",
        str(ids["candidate"]),
        "--goal",
        goal,
        "--handoff-token",
        token,
        "--yes",
        "--output",
        "json",
    ]


class _Counts:
    def __init__(self) -> None:
        self.sessions = 0
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.commit_failure = False
        self.before_commit: Callable[[Session], None] | None = None

    def reset(self) -> None:
        self.sessions = 0
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0


def _install_real_executor(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    *,
    commit_failure: bool = False,
    before_commit: Callable[[Session], None] | None = None,
) -> _Counts:
    counts = _Counts()
    counts.commit_failure = commit_failure
    counts.before_commit = before_commit
    real_execute = agent_cli._execute_agent_contact_apply

    def session_factory() -> Session:
        counts.sessions += 1
        session = factory()
        real_commit = session.commit
        real_rollback = session.rollback
        real_close = session.close

        def commit() -> None:
            counts.commits += 1
            if counts.before_commit is not None:
                counts.before_commit(session)
            if counts.commit_failure:
                raise RuntimeError("controlled commit failure")
            real_commit()

        def rollback() -> None:
            counts.rollbacks += 1
            real_rollback()

        def close() -> None:
            counts.closes += 1
            real_close()

        session.commit = commit
        session.rollback = rollback
        session.close = close
        return session

    def execute(data, output: str) -> str:
        return real_execute(data, output, session_factory=session_factory)

    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", execute)
    return counts


def _counts(factory: sessionmaker[Session]) -> tuple[int, int, int]:
    with factory() as session:
        return (
            session.scalar(select(func.count()).select_from(Contact)) or 0,
            session.scalar(select(func.count()).select_from(Lead)) or 0,
            session.scalar(select(func.count()).select_from(Task)) or 0,
        )


def test_fresh_and_repeated_public_cli_apply_are_idempotent(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, ids = database
    token = _token(factory, ids)
    counts = _install_real_executor(monkeypatch, factory)
    first = runner.invoke(app, _arguments(ids, token))
    second = runner.invoke(app, _arguments(ids, token))
    assert first.exit_code == second.exit_code == 0
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["network_call_count"] == second_payload["network_call_count"] == 0
    assert (
        first_payload["contact_created"],
        first_payload["lead_created"],
        first_payload["task_created"],
    ) == (True, True, True)
    assert (
        second_payload["contact_created"],
        second_payload["lead_created"],
        second_payload["task_created"],
    ) == (False, False, False)
    assert tuple(first_payload[key] for key in ("contact_id", "lead_id", "task_id")) == tuple(
        second_payload[key] for key in ("contact_id", "lead_id", "task_id")
    )
    assert _counts(factory) == (1, 1, 1)
    assert (counts.commits, counts.rollbacks, counts.closes) == (2, 0, 2)
    with factory() as session:
        candidate = session.get(ContactDiscoveryCandidate, ids["candidate"])
        contact = session.get(Contact, first_payload["contact_id"])
        lead = session.get(Lead, first_payload["lead_id"])
        task = session.get(Task, first_payload["task_id"])
        assert candidate is not None and candidate.promoted_contact_id == contact.id
        assert contact is not None and contact.company_id == ids["company"]
        assert (
            lead is not None and lead.company_id == ids["company"] and lead.contact_id == contact.id
        )
        assert task is not None and task.lead_id == lead.id


@pytest.mark.parametrize("mutation", ["goal", "generation", "candidate", "company", "source"])
def test_stale_handoff_fields_leave_no_mutation(
    database, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    factory, ids = database
    token = _token(factory, ids)
    goal = GOAL
    if mutation == "goal":
        goal = "Different goal"
    else:
        with factory() as session:
            if mutation == "generation":
                state = session.get(CompanyContactDiscoveryState, ids["company"])
                assert state is not None
                state.checked_at += timedelta(seconds=1)
            elif mutation == "candidate":
                candidate = session.get(ContactDiscoveryCandidate, ids["candidate"])
                assert candidate is not None
                candidate.title = "CEO"
            elif mutation == "company":
                company = session.get(Company, ids["company"])
                assert company is not None
                company.name = "Changed"
            else:
                candidate = session.get(ContactDiscoveryCandidate, ids["candidate"])
                assert candidate is not None
                candidate.confidence = 81
            session.commit()
    counts = _install_real_executor(monkeypatch, factory)
    outcome = runner.invoke(app, _arguments(ids, token, goal=goal))
    assert outcome.exit_code == 5
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent contact apply handoff is stale.\n"
    assert _counts(factory) == (0, 0, 0)
    with factory() as session:
        candidate = session.get(ContactDiscoveryCandidate, ids["candidate"])
        assert candidate is not None
        assert candidate.promoted_contact_id is None
        assert candidate.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED.value
    assert (counts.commits, counts.rollbacks, counts.closes) == (0, 1, 1)


@pytest.mark.parametrize(
    "scope",
    [
        "foreign_project",
        "company_outside_project",
        "candidate_outside_company",
        "candidate_outside_project",
    ],
)
def test_foreign_scope_is_rejected_without_mutation(
    database, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    factory, ids = database
    token = _token(factory, ids)
    arguments = _arguments(ids, token)
    if scope == "foreign_project":
        arguments[arguments.index("--project-id") + 1] = str(ids["foreign_project"])
    elif scope == "company_outside_project":
        arguments[arguments.index("--company-id") + 1] = str(ids["foreign_company"])
    elif scope == "candidate_outside_company":
        arguments[arguments.index("--candidate-id") + 1] = str(ids["peer_candidate"])
    else:
        arguments[arguments.index("--candidate-id") + 1] = str(ids["foreign_candidate"])
    counts = _install_real_executor(monkeypatch, factory)
    outcome = runner.invoke(app, arguments)
    assert outcome.exit_code == 4
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent contact apply target was not found.\n"
    assert _counts(factory) == (0, 0, 0)
    assert (counts.commits, counts.rollbacks, counts.closes) == (0, 1, 1)


def test_late_task_failure_rolls_back_all_mutations(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, ids = database
    token = _token(factory, ids)
    real_task_repository = TaskRepository

    def task_repository(session: Session):
        repository = real_task_repository(session)

        def fail(**values: object):
            raise RuntimeError("controlled late task failure")

        repository.create_for_lead = fail
        return repository

    monkeypatch.setattr(agent_cli, "TaskRepository", task_repository)
    counts = _install_real_executor(monkeypatch, factory)
    outcome = runner.invoke(app, _arguments(ids, token))
    assert outcome.exit_code == 9
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent contact apply could not be persisted.\n"
    assert _counts(factory) == (0, 0, 0)
    with factory() as session:
        candidate = session.get(ContactDiscoveryCandidate, ids["candidate"])
        assert candidate is not None
        assert candidate.promoted_contact_id is None
        assert candidate.discovery_status == ContactDiscoveryCandidateStatus.DISCOVERED.value
    assert (counts.commits, counts.rollbacks, counts.closes) == (0, 1, 1)


def test_commit_failure_has_no_success_output_and_no_retry(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, ids = database
    token = _token(factory, ids)
    counts = _install_real_executor(monkeypatch, factory, commit_failure=True)
    outcome = runner.invoke(app, _arguments(ids, token))
    assert outcome.exit_code == 9
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent contact apply could not be persisted.\n"
    assert _counts(factory) == (0, 0, 0)
    assert (counts.commits, counts.rollbacks, counts.closes) == (1, 1, 1)


def test_caller_commit_failure_after_legacy_normalization_rolls_back(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, ids = database
    token = _token(factory, ids)
    counts = _install_real_executor(monkeypatch, factory)

    seeded = runner.invoke(app, _arguments(ids, token))
    assert seeded.exit_code == 0
    seeded_payload = json.loads(seeded.stdout)
    contact_id = seeded_payload["contact_id"]
    lead_id = seeded_payload["lead_id"]
    task_id = seeded_payload["task_id"]
    proposals = build_contact_plan_proposals(
        company_name="Acme",
        candidate_name="Ada Lovelace",
        candidate_title="Founder",
        goal=GOAL,
    )
    legacy_description = build_legacy_contact_plan_task_description(
        company_name="Acme",
        candidate_name="Ada Lovelace",
        candidate_title="Founder",
        goal=GOAL,
    )
    with factory() as setup:
        task = setup.get(Task, task_id)
        contact = setup.get(Contact, contact_id)
        lead = setup.get(Lead, lead_id)
        candidate = setup.get(ContactDiscoveryCandidate, ids["candidate"])
        assert task is not None and contact is not None and lead is not None
        assert candidate is not None
        assert task.title == proposals.task_title
        assert task.description == proposals.task_description
        assert task.status == "TODO"
        assert task.due_at is None
        task.description = legacy_description
        setup.commit()
        contact_state = (
            contact.company_id,
            contact.first_name,
            contact.last_name,
            contact.job_title,
            contact.email,
            contact.phone,
            contact.source,
            contact.external_id,
            contact.status,
        )
        lead_state = (lead.company_id, lead.contact_id, lead.status, lead.source, lead.notes)
        candidate_state = (
            candidate.discovery_status,
            candidate.promoted_contact_id,
            candidate.name,
            candidate.title,
            candidate.email,
            candidate.phone,
            candidate.deduplication_key,
        )

    observations = {"render_calls": 0, "commit_observations": 0}
    rendered_results: list[str] = []
    real_render = agent_cli.render_agent_contact_apply

    def render(result: AgentContactApplyResult, output: str) -> str:
        observations["render_calls"] += 1
        assert result.contact_id == contact_id
        assert result.lead_id == lead_id
        assert result.task_id == task_id
        assert (result.contact_created, result.lead_created, result.task_created) == (
            False,
            False,
            False,
        )
        assert (result.contact_reused, result.lead_reused, result.task_reused) == (
            True,
            True,
            True,
        )
        assert (result.task_mutation_count, result.crm_mutated) == (1, True)
        rendered = real_render(result, output)
        rendered_results.append(rendered)
        return rendered

    def observe_normalized_state(session: Session) -> None:
        observations["commit_observations"] += 1
        assert observations["render_calls"] == 1
        assert len(rendered_results) == 1
        payload = json.loads(rendered_results[0])
        assert payload["task_id"] == task_id
        tasks = session.scalars(select(Task).where(Task.lead_id == lead_id)).all()
        assert len(tasks) == 1
        assert tasks[0].id == task_id
        assert tasks[0].description == proposals.task_description
        assert tasks[0].description != legacy_description

    monkeypatch.setattr(agent_cli, "render_agent_contact_apply", render)
    counts.reset()
    counts.commit_failure = True
    counts.before_commit = observe_normalized_state

    outcome = runner.invoke(app, _arguments(ids, token))

    assert outcome.exit_code == 9
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent contact apply could not be persisted.\n"
    assert "controlled commit failure" not in outcome.stderr
    assert "Traceback" not in outcome.stderr
    assert observations == {"render_calls": 1, "commit_observations": 1}
    assert len(rendered_results) == 1
    assert counts.sessions == 1
    assert (counts.commits, counts.rollbacks, counts.closes) == (1, 1, 1)

    with factory() as verification:
        task = verification.get(Task, task_id)
        contact = verification.get(Contact, contact_id)
        lead = verification.get(Lead, lead_id)
        candidate = verification.get(ContactDiscoveryCandidate, ids["candidate"])
        assert task is not None and contact is not None and lead is not None
        assert candidate is not None
        assert task.id == task_id
        assert task.description == legacy_description
        assert task.description != proposals.task_description
        tasks = verification.scalars(select(Task).where(Task.lead_id == lead_id)).all()
        assert len(tasks) == 1
        assert (
            contact.company_id,
            contact.first_name,
            contact.last_name,
            contact.job_title,
            contact.email,
            contact.phone,
            contact.source,
            contact.external_id,
            contact.status,
        ) == contact_state
        assert (
            lead.company_id,
            lead.contact_id,
            lead.status,
            lead.source,
            lead.notes,
        ) == lead_state
        assert (
            candidate.discovery_status,
            candidate.promoted_contact_id,
            candidate.name,
            candidate.title,
            candidate.email,
            candidate.phone,
            candidate.deduplication_key,
        ) == candidate_state
        assert verification.scalar(select(func.count()).select_from(Contact)) == 1
        assert verification.scalar(select(func.count()).select_from(Lead)) == 1
        assert verification.scalar(select(func.count()).select_from(Task)) == 1

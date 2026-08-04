import json
import socket
import urllib.request
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import openai
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.database.base import Base
from app.modules.agent import (
    AgentCompanySelectionInvalidDataError,
    AgentCompanySelectionNoCandidatesError,
    AgentCompanySelectionRunNotFoundError,
    AgentCompanySelectionRunNotReadyError,
    AgentCompanySelectionService,
)
from app.modules.agent.company_selection import AgentCompanySelectionRepository
from app.modules.company.models import Company
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
)
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.search_profile.models import SearchProfile
from app.modules.task.models import Task
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionCandidate,
    OpenAIDecisionKind,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)


@pytest.fixture
def session(tmp_path: Path) -> Generator[Session]:
    database = tmp_path / "agent-selection.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as value:
            yield value
    finally:
        engine.dispose()


def project_and_profile(session: Session, name: str) -> tuple[Project, SearchProfile]:
    project = Project(name=name)
    session.add(project)
    session.flush()
    profile = SearchProfile(
        project_id=project.id,
        name=f"{name} profile",
        product_or_service="Security automation",
    )
    session.add(profile)
    session.flush()
    return project, profile


def create_run(
    repository: CompanyDiscoveryStagingRepository,
    project: Project,
    profile: SearchProfile,
    status: CompanyDiscoveryRunStatus,
) -> CompanyDiscoveryRun:
    run = repository.create_run(
        CompanyDiscoveryRunCreate(
            project_id=project.id,
            search_profile_id=profile.id,
            provider="serpapi",
            request_snapshot=CompanyDiscoveryRequestSnapshot(
                source_mode="SEARCH_PROFILE",
                search_profile_id=profile.id,
                country_codes=["US"],
                query_count=1,
                result_limit=5,
                total_result_ceiling=5,
            ),
        )
    )
    repository.update_run(
        run.id,
        CompanyDiscoveryRunUpdate(run_status=status, completed_at=datetime.now(UTC)),
    )
    return run


def create_candidate(
    repository: CompanyDiscoveryStagingRepository,
    project_id: int,
    run_id: int,
    number: int,
    *,
    position: int | None,
) -> CompanyDiscoveryCandidate:
    result = repository.upsert_candidate(
        project_id,
        run_id,
        CompanyDiscoveryCandidateCreate(
            project_id=project_id,
            run_id=run_id,
            provider="serpapi",
            name=f"Candidate {number}",
            website=f"https://candidate-{number}.example",
            country_code="US",
            position=position,
        ),
    )
    return repository.session.get_one(CompanyDiscoveryCandidate, result.candidate.id)


def install_network_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network or external client use is forbidden")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    monkeypatch.setattr(openai, "OpenAI", blocked)


def select_decision(index: int) -> OpenAIDecisionResult:
    return OpenAIDecisionResult(
        decision=OpenAIDecisionKind.SELECT,
        selected_candidate_index=index,
        confidence=0.9,
        company_fit=OpenAICompanyFit.HIGH,
        rationale="Best persisted candidate",
        next_action_title="Review",
        next_action_description="Confirm selection",
        human_review_required=True,
    )


def no_selection_decision() -> OpenAIDecisionResult:
    return OpenAIDecisionResult(
        decision=OpenAIDecisionKind.NO_SELECTION,
        selected_candidate_index=None,
        confidence=0.1,
        company_fit=OpenAICompanyFit.NOT_SUITABLE,
        rationale="No suitable candidate",
        next_action_title=None,
        next_action_description=None,
        human_review_required=True,
    )


def test_real_sqlite_run_scoped_selection_is_deterministic_read_only_and_bounded(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, profile = project_and_profile(session, "Primary")
    other_project, other_profile = project_and_profile(session, "Other")
    repository = CompanyDiscoveryStagingRepository(session)
    old_run = create_run(repository, project, profile, CompanyDiscoveryRunStatus.SUCCEEDED)
    run = create_run(repository, project, profile, CompanyDiscoveryRunStatus.SUCCEEDED)
    other_run = create_run(
        repository, other_project, other_profile, CompanyDiscoveryRunStatus.SUCCEEDED
    )

    repeated = create_candidate(repository, project.id, old_run.id, 100, position=4)
    repository.upsert_candidate(
        project.id,
        run.id,
        CompanyDiscoveryCandidateCreate(
            project_id=project.id,
            run_id=run.id,
            provider="serpapi",
            name="Candidate 100",
            website="https://candidate-100.example",
            country_code="US",
            position=2,
        ),
    )
    eligible = [
        create_candidate(repository, project.id, run.id, number, position=position)
        for number, position in ((1, None), (2, 1), (3, 3), (4, 2), (5, 4), (6, 5))
    ]
    reviewed = create_candidate(repository, project.id, run.id, 20, position=1)
    rejected = create_candidate(repository, project.id, run.id, 21, position=1)
    promoted = create_candidate(repository, project.id, run.id, 22, position=1)
    reviewed.candidate_status = CompanyDiscoveryCandidateStatus.REVIEWED
    rejected.candidate_status = CompanyDiscoveryCandidateStatus.REJECTED
    promoted.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
    create_candidate(repository, other_project.id, other_run.id, 30, position=1)
    session.commit()

    candidate_snapshot = {
        row.id: (row.candidate_status, row.last_seen_run_id, row.promoted_company_id)
        for row in session.scalars(select(CompanyDiscoveryCandidate))
    }
    run_snapshot = {
        row.id: (str(row.run_status), row.candidate_count, row.updated_at.replace(tzinfo=None))
        for row in session.scalars(select(CompanyDiscoveryRun))
    }
    install_network_guards(monkeypatch)

    selection_service = AgentCompanySelectionService(
        cast(AgentCompanySelectionRepository, repository)
    )
    selection = selection_service.prepare(
        project_id=project.id,
        run_id=run.id,
        goal="Choose the strongest fit",
        max_candidates=5,
    )

    expected_ids = [eligible[1].id, repeated.id, eligible[3].id, eligible[2].id, eligible[4].id]
    assert [binding.candidate_id for binding in selection.bindings] == expected_ids
    assert [candidate.index for candidate in selection.request.candidates] == [1, 2, 3, 4, 5]
    assert (
        selection_service.resolve_selected_candidate_id(selection, select_decision(2))
        == repeated.id
    )
    assert (
        selection_service.resolve_selected_candidate_id(selection, no_selection_decision()) is None
    )
    assert reviewed.id not in expected_ids
    assert rejected.id not in expected_ids
    assert promoted.id not in expected_ids

    session.rollback()
    session.commit()
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Task)) == 0
    assert {
        row.id: (row.candidate_status, row.last_seen_run_id, row.promoted_company_id)
        for row in session.scalars(select(CompanyDiscoveryCandidate))
    } == candidate_snapshot
    assert {
        row.id: (str(row.run_status), row.candidate_count, row.updated_at.replace(tzinfo=None))
        for row in session.scalars(select(CompanyDiscoveryRun))
    } == run_snapshot


@pytest.mark.parametrize(
    "status,error",
    [
        (CompanyDiscoveryRunStatus.NOT_FOUND, AgentCompanySelectionNoCandidatesError),
        (CompanyDiscoveryRunStatus.PENDING, AgentCompanySelectionRunNotReadyError),
        (CompanyDiscoveryRunStatus.FAILED, AgentCompanySelectionRunNotReadyError),
    ],
)
def test_real_sqlite_rejects_non_ready_run_before_candidate_read(
    session: Session,
    status: CompanyDiscoveryRunStatus,
    error: type[Exception],
) -> None:
    project, profile = project_and_profile(session, status.value)
    repository = CompanyDiscoveryStagingRepository(session)
    run = create_run(repository, project, profile, status)
    with pytest.raises(error):
        AgentCompanySelectionService(cast(AgentCompanySelectionRepository, repository)).prepare(
            project_id=project.id, run_id=run.id, goal="Choose"
        )


def test_real_sqlite_cross_project_run_is_not_disclosed(session: Session) -> None:
    first, first_profile = project_and_profile(session, "First")
    second, _second_profile = project_and_profile(session, "Second")
    repository = CompanyDiscoveryStagingRepository(session)
    run = create_run(repository, first, first_profile, CompanyDiscoveryRunStatus.SUCCEEDED)
    with pytest.raises(AgentCompanySelectionRunNotFoundError):
        AgentCompanySelectionService(cast(AgentCompanySelectionRepository, repository)).prepare(
            project_id=second.id, run_id=run.id, goal="Choose"
        )


def test_corrective_oversized_persisted_values_are_rejected_without_session_mutation(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, profile = project_and_profile(session, "Oversized")
    repository = CompanyDiscoveryStagingRepository(session)
    oversized_run = create_run(
        repository,
        project,
        profile,
        CompanyDiscoveryRunStatus.SUCCEEDED,
    )
    oversized_candidates = [
        create_candidate(repository, project.id, oversized_run.id, number, position=number)
        for number in range(1, 6)
    ]
    for candidate in oversized_candidates:
        candidate.name = "N" + "\x01" * 199
        candidate.website = "\x02" * 500
        assert len(candidate.name) <= 255
        assert len(candidate.website) <= 500

    expected_request = OpenAIDecisionRequest(
        goal="Choose",
        candidates=tuple(
            OpenAIDecisionCandidate(
                index=index,
                name=cast(str, candidate.name),
                website=candidate.website,
                country=candidate.country_code,
                city=None,
                industry=None,
                snippet=None,
                website_summary=None,
            )
            for index, candidate in enumerate(oversized_candidates, start=1)
        ),
    )
    expected_serialized = json.dumps(
        expected_request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(expected_serialized.encode("utf-8")) > 20_000

    ordinary_run = create_run(
        repository,
        project,
        profile,
        CompanyDiscoveryRunStatus.SUCCEEDED,
    )
    ordinary_candidate = create_candidate(
        repository,
        project.id,
        ordinary_run.id,
        99,
        position=1,
    )
    session.commit()

    run_snapshot = {
        row.id: (str(row.run_status), row.candidate_count, row.updated_at.replace(tzinfo=None))
        for row in session.scalars(select(CompanyDiscoveryRun))
    }
    candidate_snapshot = {
        row.id: (
            row.name,
            row.website,
            str(row.candidate_status),
            row.last_seen_run_id,
            row.promoted_company_id,
        )
        for row in session.scalars(select(CompanyDiscoveryCandidate))
    }
    install_network_guards(monkeypatch)
    selection_service = AgentCompanySelectionService(
        cast(AgentCompanySelectionRepository, repository)
    )

    with pytest.raises(
        AgentCompanySelectionInvalidDataError,
        match="^Agent company selection data is invalid\\.$",
    ):
        selection_service.prepare(
            project_id=project.id,
            run_id=oversized_run.id,
            goal="Choose",
            max_candidates=5,
        )

    ordinary_selection = selection_service.prepare(
        project_id=project.id,
        run_id=ordinary_run.id,
        goal="Choose",
        max_candidates=5,
    )
    assert (
        selection_service.resolve_selected_candidate_id(
            ordinary_selection,
            select_decision(1),
        )
        == ordinary_candidate.id
    )

    session.rollback()
    session.commit()
    bind = session.get_bind()
    with Session(bind, expire_on_commit=False) as verification:
        assert {
            row.id: (
                str(row.run_status),
                row.candidate_count,
                row.updated_at.replace(tzinfo=None),
            )
            for row in verification.scalars(select(CompanyDiscoveryRun))
        } == run_snapshot
        assert {
            row.id: (
                row.name,
                row.website,
                str(row.candidate_status),
                row.last_seen_run_id,
                row.promoted_company_id,
            )
            for row in verification.scalars(select(CompanyDiscoveryCandidate))
        } == candidate_snapshot
        assert verification.scalar(select(func.count()).select_from(Company)) == 0
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_persisted_candidate_surrogate_is_sanitized_without_mutation(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, profile = project_and_profile(session, "Surrogate")
    repository = CompanyDiscoveryStagingRepository(session)
    run = create_run(repository, project, profile, CompanyDiscoveryRunStatus.SUCCEEDED)
    candidate = create_candidate(repository, project.id, run.id, 1, position=1)
    session.commit()
    original_website = candidate.website
    install_network_guards(monkeypatch)
    candidate.website = "https://example.test/\ud800"

    with (
        session.no_autoflush,
        pytest.raises(
            AgentCompanySelectionInvalidDataError,
            match="^Agent company selection data is invalid\\.$",
        ) as exc_info,
    ):
        AgentCompanySelectionService(cast(AgentCompanySelectionRepository, repository)).prepare(
            project_id=project.id,
            run_id=run.id,
            goal="Choose",
            max_candidates=5,
        )

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    session.rollback()
    bind = session.get_bind()
    with Session(bind, expire_on_commit=False) as verification:
        persisted = verification.get_one(CompanyDiscoveryCandidate, candidate.id)
        assert persisted.website == original_website
        assert verification.scalar(select(func.count()).select_from(Company)) == 0
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0


def test_persisted_valid_unicode_prepares_read_only(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, profile = project_and_profile(session, "Unicode")
    repository = CompanyDiscoveryStagingRepository(session)
    run = create_run(repository, project, profile, CompanyDiscoveryRunStatus.SUCCEEDED)
    candidate = create_candidate(repository, project.id, run.id, 1, position=1)
    candidate.name = "Компания Việt Nam 🙂"
    candidate.website = "https://пример.test/مرحبا"
    session.commit()
    install_network_guards(monkeypatch)

    selection = AgentCompanySelectionService(
        cast(AgentCompanySelectionRepository, repository)
    ).prepare(
        project_id=project.id,
        run_id=run.id,
        goal="Выбрать أفضل компанию 🙂",
        max_candidates=5,
    )

    assert selection.request.candidates[0].name == "Компания Việt Nam 🙂"
    assert selection.request.candidates[0].website == "https://пример.test/مرحبا"
    session.rollback()
    bind = session.get_bind()
    with Session(bind, expire_on_commit=False) as verification:
        persisted = verification.get_one(CompanyDiscoveryCandidate, candidate.id)
        assert persisted.name == "Компания Việt Nam 🙂"
        assert persisted.website == "https://пример.test/مرحبا"
        assert verification.scalar(select(func.count()).select_from(Company)) == 0
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0
        assert verification.scalar(select(func.count()).select_from(Lead)) == 0
        assert verification.scalar(select(func.count()).select_from(Task)) == 0

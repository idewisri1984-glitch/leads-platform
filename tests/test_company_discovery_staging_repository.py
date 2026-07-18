from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_repository import (
    CompanyDiscoveryStagingRepository,
)
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
    CompanyDiscoverySourceMode,
)
from app.modules.contact.models import Contact
from app.modules.project.models import Project
from app.modules.search_profile.models import SearchProfile


@pytest.fixture
def session() -> Generator[Session]:
    with SessionLocal() as value:
        yield value


def make_project(session: Session, name: str = "Project") -> Project:
    project = Project(name=name)
    session.add(project)
    session.flush()
    return project


def make_profile(session: Session, project_id: int) -> SearchProfile:
    profile = SearchProfile(
        project_id=project_id,
        name="Profile",
        product_or_service="Service",
    )
    session.add(profile)
    session.flush()
    return profile


def run_data(project_id: int, profile_id: int | None = None) -> CompanyDiscoveryRunCreate:
    mode = (
        CompanyDiscoverySourceMode.SEARCH_PROFILE
        if profile_id is not None
        else CompanyDiscoverySourceMode.AD_HOC
    )
    return CompanyDiscoveryRunCreate(
        project_id=project_id,
        search_profile_id=profile_id,
        provider="serpapi",
        request_snapshot=CompanyDiscoveryRequestSnapshot(
            source_mode=mode,
            search_profile_id=profile_id,
            country_codes=["US"],
            query_count=1,
            result_limit=10,
            total_result_ceiling=10,
        ),
    )


def candidate_data(
    project_id: int, run_id: int, **changes: object
) -> CompanyDiscoveryCandidateCreate:
    values: dict[str, object] = {
        "project_id": project_id,
        "run_id": run_id,
        "provider": "serpapi",
        "name": "Acme",
        "website": "https://www.example.com/about",
        "country_code": "US",
        "position": 5,
    }
    values.update(changes)
    return CompanyDiscoveryCandidateCreate(**values)


def test_create_update_and_list_run_flush_without_commit(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    assert run.id is not None
    assert run.request_fingerprint == run_data(project.id).request_snapshot.fingerprint()
    assert repository.get_run(run.id) is run
    assert repository.list_runs_for_project(project.id, 10) == [run]
    repository.update_run(
        run.id,
        CompanyDiscoveryRunUpdate(
            run_status=CompanyDiscoveryRunStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            result_count=2,
        ),
    )
    assert run.run_status == CompanyDiscoveryRunStatus.SUCCEEDED
    session.rollback()
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 0


def test_run_accepts_same_project_profile_and_rejects_cross_project(session: Session) -> None:
    first, second = make_project(session, "First"), make_project(session, "Second")
    profile = make_profile(session, first.id)
    repository = CompanyDiscoveryStagingRepository(session)
    assert repository.create_run(run_data(first.id, profile.id)).search_profile_id == profile.id
    with pytest.raises(ValueError):
        repository.create_run(run_data(second.id, profile.id))


def test_upsert_is_idempotent_and_tracks_repeated_runs(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    second_run = repository.create_run(run_data(project.id))
    first = repository.upsert_candidate(
        project.id, first_run.id, candidate_data(project.id, first_run.id)
    )
    repeated = repository.upsert_candidate(
        project.id, first_run.id, candidate_data(project.id, first_run.id)
    )
    observed = repository.upsert_candidate(
        project.id,
        second_run.id,
        candidate_data(project.id, second_run.id, website="http://example.com/", position=2),
    )
    assert first.created is True
    assert repeated.created is False and repeated.updated is False
    assert observed.updated is True
    assert observed.candidate.first_seen_run_id == first_run.id
    assert observed.candidate.last_seen_run_id == second_run.id
    assert observed.candidate.best_position == 2
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate)) == 1


def test_worse_position_and_nonempty_fields_are_not_overwritten(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))
    result = repository.upsert_candidate(
        project.id,
        run.id,
        candidate_data(project.id, run.id, name="Other", position=9),
    )
    assert result.updated is False
    assert result.candidate.name == "Acme"
    assert result.candidate.best_position == 5


@pytest.mark.parametrize(
    "status",
    [
        CompanyDiscoveryCandidateStatus.REVIEWED,
        CompanyDiscoveryCandidateStatus.PROMOTED,
        CompanyDiscoveryCandidateStatus.REJECTED,
    ],
)
def test_protected_candidate_updates_only_safe_observation_fields(
    session: Session, status: CompanyDiscoveryCandidateStatus
) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    second_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id, first_run.id, candidate_data(project.id, first_run.id)
    )
    model = repository.get_candidate(created.candidate.id)
    assert model is not None
    model.candidate_status = status
    model.promoted_company_id = None
    session.flush()
    result = repository.upsert_candidate(
        project.id,
        second_run.id,
        candidate_data(project.id, second_run.id, name="Overwrite", position=1),
    )
    assert result.protected is True and result.updated is True
    assert result.candidate.candidate_status == status
    assert result.candidate.name == "Acme"
    assert result.candidate.first_seen_run_id == first_run.id
    assert result.candidate.last_seen_run_id == second_run.id
    assert result.candidate.best_position == 1


def test_cross_project_scope_and_invalid_candidate_mutate_nothing(session: Session) -> None:
    first, second = make_project(session, "First"), make_project(session, "Second")
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(first.id))
    with pytest.raises(ValueError):
        repository.upsert_candidate(second.id, run.id, candidate_data(second.id, run.id))
    with pytest.raises(ValueError):
        repository.upsert_candidate(
            first.id,
            run.id,
            candidate_data(first.id, run.id, website=None, name=None, country_code=None),
        )
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate)) == 0


def test_same_identity_is_project_scoped_and_canonical_rows_are_untouched(session: Session) -> None:
    first, second = make_project(session, "First"), make_project(session, "Second")
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(first.id))
    second_run = repository.create_run(run_data(second.id))
    repository.upsert_candidate(first.id, first_run.id, candidate_data(first.id, first_run.id))
    repository.upsert_candidate(second.id, second_run.id, candidate_data(second.id, second_run.id))
    assert session.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate)) == 2
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_caller_commit_persists_complete_rows(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))
    candidate_id = created.candidate.id
    session.commit()
    session.expunge_all()
    assert session.get(CompanyDiscoveryCandidate, candidate_id) is not None

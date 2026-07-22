from collections.abc import Generator, Sequence
from datetime import UTC, datetime
from typing import cast

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
    CompanyDiscoveryStagingNotFoundError,
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


def test_repository_never_controls_session_transaction_or_lifecycle(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(session)
    original_flush = session.flush
    calls = {"flush": 0, "commit": 0, "rollback": 0, "close": 0}

    def track_flush(objects: Sequence[object] | None = None) -> None:
        calls["flush"] += 1
        original_flush(objects)

    def track_commit() -> None:
        calls["commit"] += 1

    def track_rollback() -> None:
        calls["rollback"] += 1

    def track_close() -> None:
        calls["close"] += 1

    monkeypatch.setattr(session, "flush", track_flush)
    monkeypatch.setattr(session, "commit", track_commit)
    monkeypatch.setattr(session, "rollback", track_rollback)
    monkeypatch.setattr(session, "close", track_close)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    repository.update_run(run.id, CompanyDiscoveryRunUpdate(result_count=1))
    repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))
    assert calls == {"flush": 3, "commit": 0, "rollback": 0, "close": 0}


def test_caller_rollback_atomically_removes_run_and_candidate(session: Session) -> None:
    project = make_project(session)
    project_id = project.id
    session.commit()
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project_id))
    run_id = run.id
    result = repository.upsert_candidate(project_id, run_id, candidate_data(project_id, run_id))
    candidate_id = result.candidate.id
    assert session.get(CompanyDiscoveryRun, run_id) is not None
    assert session.get(CompanyDiscoveryCandidate, candidate_id) is not None
    session.rollback()

    with SessionLocal() as verification:
        assert verification.get(Project, project_id) is not None
        assert verification.get(CompanyDiscoveryRun, run_id) is None
        assert verification.get(CompanyDiscoveryCandidate, candidate_id) is None
        assert verification.scalar(select(func.count()).select_from(Company)) == 0
        assert verification.scalar(select(func.count()).select_from(Contact)) == 0


def test_discovered_candidate_fills_all_empty_safe_fields_consistently(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    second_run = repository.create_run(run_data(project.id))
    existing = CompanyDiscoveryCandidate(
        project_id=project.id,
        first_seen_run_id=first_run.id,
        last_seen_run_id=first_run.id,
        provider="original-provider",
        identity_key="website:example.com",
        best_position=8,
    )
    session.add(existing)
    session.flush()

    incoming = candidate_data(
        project.id,
        second_run.id,
        provider="different-provider",
        name="  ACME   Incorporated ",
        website="https://www.example.com/about?source=test#fragment",
        country_code="us",
        position=3,
    )
    result = repository.upsert_candidate(project.id, second_run.id, incoming)
    assert result.updated is True and result.protected is False
    assert result.candidate.name == "ACME Incorporated"
    assert result.candidate.normalized_name == "acme incorporated"
    assert result.candidate.website == "https://www.example.com/about?source=test"
    assert result.candidate.website_identity == "example.com"
    assert result.candidate.country_code == "US"
    assert result.candidate.identity_key == "website:example.com"
    assert result.candidate.first_seen_run_id == first_run.id
    assert result.candidate.last_seen_run_id == second_run.id
    assert result.candidate.best_position == 3
    assert result.candidate.candidate_status == CompanyDiscoveryCandidateStatus.DISCOVERED
    assert result.candidate.promoted_company_id is None
    assert result.candidate.provider == "original-provider"


def test_promoted_candidate_preserves_non_null_company_and_all_protected_fields(
    session: Session,
) -> None:
    project = make_project(session)
    company = Company(project_id=project.id, name="Canonical Company")
    session.add(company)
    session.flush()
    original_company_name = company.name
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(project.id))
    second_run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id, first_run.id, candidate_data(project.id, first_run.id, position=7)
    )
    model = repository.get_candidate(created.candidate.id)
    assert model is not None
    model.candidate_status = CompanyDiscoveryCandidateStatus.PROMOTED
    model.promoted_company_id = company.id
    session.flush()
    original_identity = model.identity_key

    incoming = candidate_data(
        project.id,
        second_run.id,
        provider="other-provider",
        name="Conflicting Name",
        website="http://example.com/conflict",
        country_code="GB",
        position=2,
    )
    updated = repository.upsert_candidate(project.id, second_run.id, incoming)
    assert updated.protected is True and updated.updated is True
    assert updated.candidate.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED
    assert updated.candidate.promoted_company_id == company.id
    assert updated.candidate.identity_key == original_identity
    assert updated.candidate.first_seen_run_id == first_run.id
    assert updated.candidate.last_seen_run_id == second_run.id
    assert updated.candidate.name == "Acme"
    assert updated.candidate.normalized_name == "acme"
    assert updated.candidate.website == "https://www.example.com/about"
    assert updated.candidate.country_code == "US"
    assert updated.candidate.provider == "serpapi"
    assert updated.candidate.best_position == 2

    unchanged = repository.upsert_candidate(project.id, second_run.id, incoming)
    assert unchanged.protected is True and unchanged.updated is False
    assert unchanged.candidate.promoted_company_id == company.id
    assert company.name == original_company_name


def test_get_candidate_for_project_is_scoped_by_project_id(session: Session) -> None:
    first, second = make_project(session, "First"), make_project(session, "Second")
    repository = CompanyDiscoveryStagingRepository(session)
    first_run = repository.create_run(run_data(first.id))
    created = repository.upsert_candidate(
        first.id, first_run.id, candidate_data(first.id, first_run.id, name="Acme")
    )

    assert repository.get_candidate_for_project(first.id, created.candidate.id) is not None
    assert repository.get_candidate_for_project(second.id, created.candidate.id) is None


def test_set_candidate_status_allows_only_reviewed_or_rejected(
    session: Session,
) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))

    with pytest.raises(ValueError):
        repository.set_candidate_status(
            project.id, created.candidate.id, CompanyDiscoveryCandidateStatus.DISCOVERED
        )
    with pytest.raises(ValueError):
        repository.set_candidate_status(
            project.id, created.candidate.id, CompanyDiscoveryCandidateStatus.PROMOTED
        )


def test_set_candidate_status_only_updates_candidate_status(
    session: Session,
) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    company = Company(project_id=project.id, name="Existing")
    session.add(company)
    session.flush()
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(
        project.id,
        run.id,
        candidate_data(
            project.id,
            run.id,
            name="Acme",
            website="https://www.example.com",
        ),
    )
    model = repository.get_candidate(created.candidate.id)
    assert model is not None
    model.candidate_status = CompanyDiscoveryCandidateStatus.REVIEWED
    model.promoted_company_id = company.id
    session.flush()

    updated = repository.set_candidate_status(
        project.id, model.id, CompanyDiscoveryCandidateStatus.REJECTED
    )

    assert updated.candidate_status == CompanyDiscoveryCandidateStatus.REJECTED
    assert updated.promoted_company_id == company.id


def test_set_candidate_status_not_found_raises_staging_not_found_error(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    repository.create_run(run_data(project.id))

    with pytest.raises(CompanyDiscoveryStagingNotFoundError):
        repository.set_candidate_status(project.id, 99999, CompanyDiscoveryCandidateStatus.REVIEWED)


def test_set_candidate_status_rejects_invalid_identifiers(session: Session) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))

    with pytest.raises(ValueError):
        repository.set_candidate_status(
            0, created.candidate.id, CompanyDiscoveryCandidateStatus.REVIEWED
        )
    with pytest.raises(ValueError):
        repository.set_candidate_status(project.id, 0, CompanyDiscoveryCandidateStatus.REVIEWED)
    with pytest.raises(ValueError):
        repository.set_candidate_status(
            cast(int, True),
            created.candidate.id,
            CompanyDiscoveryCandidateStatus.REVIEWED,
        )


def test_set_candidate_status_does_not_control_session_transaction_or_lifecycle(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))

    calls = {"commit": 0, "rollback": 0, "close": 0}

    def track_commit() -> None:
        calls["commit"] += 1

    def track_rollback() -> None:
        calls["rollback"] += 1

    def track_close() -> None:
        calls["close"] += 1

    monkeypatch.setattr(session, "commit", track_commit)
    monkeypatch.setattr(session, "rollback", track_rollback)
    monkeypatch.setattr(session, "close", track_close)
    repository.set_candidate_status(
        project.id, created.candidate.id, CompanyDiscoveryCandidateStatus.REJECTED
    )
    assert calls == {"commit": 0, "rollback": 0, "close": 0}


def test_set_candidate_status_flush_failure_propagates(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(session)
    repository = CompanyDiscoveryStagingRepository(session)
    run = repository.create_run(run_data(project.id))
    created = repository.upsert_candidate(project.id, run.id, candidate_data(project.id, run.id))

    def failing_flush(*_objects: object) -> None:
        raise RuntimeError("flush failed")

    monkeypatch.setattr(session, "flush", failing_flush)

    with pytest.raises(RuntimeError):
        repository.set_candidate_status(
            project.id, created.candidate.id, CompanyDiscoveryCandidateStatus.REJECTED
        )

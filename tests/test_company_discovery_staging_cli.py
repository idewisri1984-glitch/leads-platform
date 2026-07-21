from typing import Any, cast

import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import company_discovery as cli
from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryStagingServiceError,
)
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidatePreview,
    CompanyDiscoveryStagingRunResult,
)
from app.modules.search_profile import SearchProfileRunOptions
from app.modules.search_profile.schemas import SearchProfileRead

runner = CliRunner()


def make_profile(*, enabled: bool = True) -> SearchProfileRead:
    return SearchProfileRead(
        id=12,
        project_id=9,
        name="Sales profile",
        description=None,
        product_or_service="Sales platform",
        target_customer_types=["accountant"],
        target_industries=[],
        positive_keywords=[],
        negative_keywords=[],
        countries=["United States"],
        cities=[],
        languages=[],
        query_templates=["{target_customer_type} {country}"],
        result_limit=10,
        max_queries_per_run=3,
        total_result_ceiling=20,
        enabled=enabled,
    )


class FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.closed = True


class FakeSessionLocal:
    calls = 0
    sessions: list[FakeSession] = []

    def __init__(self) -> None:
        type(self).calls += 1
        self.session = FakeSession()
        type(self).sessions.append(self.session)

    def __enter__(self) -> FakeSession:
        return self.session

    def __exit__(self, *args: object) -> None:
        return None


def reset_fakes() -> None:
    FakeSessionLocal.calls = 0
    FakeSessionLocal.sessions = []
    FakeSerpApiClient.calls = 0


def fake_profile_service_factory(profile: SearchProfileRead | None) -> type:
    class FakeSearchProfileService:
        def __init__(self, repository: object) -> None:
            pass

        def get(self, profile_id: int) -> SearchProfileRead | None:
            if profile is None:
                return None
            if profile_id != profile.id:
                return None
            return profile

    return FakeSearchProfileService


def fake_project_service_factory(project_exists: bool) -> type:
    class FakeProjectService:
        def __init__(self, repository: object) -> None:
            pass

        def get(self, project_id: int) -> object | None:
            if not project_exists or project_id != 9:
                return None
            return object()

    return FakeProjectService


def make_candidate(*, index: int) -> CompanyDiscoveryStagingCandidatePreview:
    return CompanyDiscoveryStagingCandidatePreview(
        name=f"Acme {index}",
        website=f"https://acme{index}.example",
        website_identity=f"https://acme{index}.example",
        country_code="US",
        best_position=index,
        identity_key=f"acme-{index}",
    )


def make_result(
    *,
    dry_run: bool,
    status: CompanyDiscoveryRunStatus,
    candidates: list[CompanyDiscoveryStagingCandidatePreview] | None = None,
    run_persisted: bool = False,
    run_id: int | None = None,
    error_code: str | None = None,
) -> CompanyDiscoveryStagingRunResult:
    result_candidates = candidates or []
    if dry_run:
        candidate_upserts = 0
        candidates_created = 0
        candidates_updated = 0
        candidates_protected = 0
    else:
        candidate_upserts = len(result_candidates)
        candidates_created = len(result_candidates)
        candidates_updated = 0
        candidates_protected = 0

    if status == CompanyDiscoveryRunStatus.PARTIAL and error_code is None:
        error_code = "candidate_invalid"
    elif status == CompanyDiscoveryRunStatus.FAILED and error_code is None:
        error_code = "execution_failed"

    return CompanyDiscoveryStagingRunResult(
        project_id=9,
        search_profile_id=12,
        profile_name="Sales profile",
        provider="serpapi",
        dry_run=dry_run,
        status=status,
        request_fingerprint="a" * 64,
        query_count=1,
        executed_queries=1,
        successful_queries=1,
        provider_result_count=1,
        provider_error_count=0,
        existing_adapter_error_count=0,
        rejected_candidate_count=0,
        duplicate_candidate_count=0,
        unique_candidate_count=len(result_candidates),
        candidate_upserts=candidate_upserts,
        candidates_created=candidates_created,
        candidates_updated=candidates_updated,
        candidates_protected=candidates_protected,
        run_id=run_id if run_persisted else None,
        run_persisted=run_persisted,
        stopped_early=False,
        stop_reason=None,
        error_code=error_code,
        candidates=result_candidates,
    )


class FakeSerpApiClient:
    calls = 0

    def __init__(self, **kwargs: object) -> None:
        type(self).calls += 1


class FailingSerpApiClient:
    def __init__(self, **kwargs: object) -> None:
        raise AssertionError("Stage command built provider unexpectedly.")


class FakeStagingService:
    def __init__(self, repository: object | None) -> None:
        pass

    def run(
        self,
        *,
        profile: SearchProfileRead,
        provider: object,
        options: SearchProfileRunOptions,
        dry_run: bool,
        repository: object | None,
    ) -> CompanyDiscoveryStagingRunResult:
        raise NotImplementedError


def patch_staging_service(
    monkeypatch: pytest.MonkeyPatch,
    result: CompanyDiscoveryStagingRunResult | None = None,
) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"options": [], "dry_run": []}

    class Service(FakeStagingService):
        def __init__(self, repository: object | None) -> None:
            self.repository = repository

        def run(
            self,
            *,
            profile: SearchProfileRead,
            provider: object,
            options: SearchProfileRunOptions,
            dry_run: bool,
            repository: object | None,
        ) -> CompanyDiscoveryStagingRunResult:
            calls["dry_run"].append(dry_run)
            calls["options"].append((profile.id, options.country_codes))
            assert profile.id == 12
            assert repository is self.repository
            assert profile.name == "Sales profile"
            assert result is not None
            return result

    monkeypatch.setattr(cli, "CompanyDiscoveryStagingService", Service)
    return calls


def test_stage_profile_command_is_registered() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "stage-profile" in result.output


def test_stage_profile_help_shows_country_option() -> None:
    result = runner.invoke(cli.app, ["stage-profile", "--help"])

    assert result.exit_code == 0
    assert "--country" in result.output


def test_stage_profile_defaults_to_dry_run_and_serpapi(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()

    calls = patch_staging_service(
        monkeypatch,
        make_result(dry_run=True, status=CompanyDiscoveryRunStatus.SUCCEEDED),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12"])

    assert result.exit_code == 0, result.output
    assert calls["dry_run"] == [True]
    assert calls["options"] == [(12, None)]
    assert FakeSerpApiClient.calls == 1
    assert FakeSessionLocal.calls == 1
    assert FakeSessionLocal.sessions[0].commit_calls == 0
    assert FakeSessionLocal.sessions[0].rollback_calls == 0
    assert "Mode: DRY_RUN" in result.output
    assert "Request Fingerprint" in result.output


def test_stage_profile_provider_name_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    patch_staging_service(
        monkeypatch,
        make_result(dry_run=True, status=CompanyDiscoveryRunStatus.SUCCEEDED),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)

    result = runner.invoke(
        cli.app,
        ["stage-profile", "--profile-id", "12", "--provider", "SeRpApI"],
    )

    assert result.exit_code == 0
    assert "Mode: DRY_RUN" in result.output


@pytest.mark.parametrize(
    ("provider", "note"),
    [
        ("other", "Unsupported provider."),
        ("", "Unsupported provider."),
    ],
)
def test_stage_profile_invalid_provider_rejected_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    note: str,
) -> None:
    reset_fakes()
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SerpApiClient", FailingSerpApiClient)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12", "--provider", provider])

    assert result.exit_code == 1
    assert note in result.output
    assert FakeSessionLocal.calls == 0


def test_stage_profile_requires_confirmation_for_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(cli, "SerpApiClient", FailingSerpApiClient)
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12", "--persist"])

    assert result.exit_code == 1
    assert "Persistence requires --yes." in result.output
    assert FakeSessionLocal.calls == 0


def test_stage_profile_rejects_yes_without_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(cli, "SerpApiClient", FailingSerpApiClient)
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12", "--yes"])

    assert result.exit_code == 1
    assert "--yes is valid only with --persist." in result.output
    assert FakeSessionLocal.calls == 0


@pytest.mark.parametrize(
    ("country",),
    [("UK",), ("ZZ",), ("",), ("Narnia",)],
)
def test_stage_profile_rejects_invalid_country_code_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    country: str,
) -> None:
    reset_fakes()
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SerpApiClient", FailingSerpApiClient)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12", "--country", country])

    assert result.exit_code == 1
    assert "Invalid staging run options." in result.output
    assert FakeSessionLocal.calls == 0


@pytest.mark.parametrize(
    ("profile", "project_exists", "message"),
    [
        (None, True, "Invalid profile ID."),
        (make_profile(enabled=False), True, "Search profile is disabled."),
        (make_profile(enabled=True), False, "Invalid profile-project relationship."),
    ],
)
def test_stage_profile_rejects_invalid_profile_state_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    profile: SearchProfileRead | None,
    project_exists: bool,
    message: str,
) -> None:
    reset_fakes()
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(profile))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(project_exists))
    monkeypatch.setattr(cli, "SerpApiClient", FailingSerpApiClient)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12"])

    assert result.exit_code == 1
    assert message in result.output
    assert FakeSerpApiClient.calls == 0
    assert FakeSessionLocal.calls >= 1


def test_stage_profile_deduplicates_and_normalizes_country_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    calls = patch_staging_service(
        monkeypatch,
        make_result(
            dry_run=True,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            candidates=[make_candidate(index=1)],
        ),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))

    result = runner.invoke(
        cli.app,
        [
            "stage-profile",
            "--profile-id",
            "12",
            "--country",
            "us",
            "--country",
            "GB",
            "--country",
            "us",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["options"][0][1] == ("GB", "US")
    assert "Mode: DRY_RUN" in result.output


def test_stage_profile_persist_success_commits_once_and_returns_zero_or_one_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    patch_staging_service(
        monkeypatch,
        make_result(
            dry_run=False,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            candidates=[make_candidate(index=1), make_candidate(index=2)],
            run_persisted=True,
            run_id=333,
        ),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)

    result = runner.invoke(
        cli.app,
        ["stage-profile", "--profile-id", "12", "--persist", "--yes"],
    )

    assert result.exit_code == 0
    assert FakeSessionLocal.calls == 1
    assert FakeSessionLocal.sessions[0].commit_calls == 1
    assert FakeSessionLocal.sessions[0].rollback_calls == 0
    assert "Mode: PERSIST" in result.output
    assert "Run ID: 333" in result.output


def test_stage_profile_persist_failed_status_commits_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    patch_staging_service(
        monkeypatch,
        make_result(
            dry_run=False,
            status=CompanyDiscoveryRunStatus.PARTIAL,
            candidates=[make_candidate(index=1)],
            run_persisted=True,
            run_id=334,
        ),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)

    result = runner.invoke(
        cli.app,
        ["stage-profile", "--profile-id", "12", "--persist", "--yes"],
    )

    assert result.exit_code == 1
    assert FakeSessionLocal.calls == 1
    assert FakeSessionLocal.sessions[0].commit_calls == 1
    assert FakeSessionLocal.sessions[0].rollback_calls == 0
    assert "status: partial" in result.output.casefold()


def test_stage_profile_persist_rollback_on_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)

    class FailingService(FakeStagingService):
        def run(
            self,
            *,
            profile: SearchProfileRead,
            provider: object,
            options: SearchProfileRunOptions,
            dry_run: bool,
            repository: object | None,
        ) -> Any:
            raise CompanyDiscoveryStagingServiceError("service failed")

    monkeypatch.setattr(cli, "CompanyDiscoveryStagingService", FailingService)

    result = runner.invoke(
        cli.app,
        ["stage-profile", "--profile-id", "12", "--persist", "--yes"],
    )

    assert result.exit_code == 1
    assert FakeSessionLocal.calls == 1
    assert FakeSessionLocal.sessions[0].commit_calls == 0
    assert FakeSessionLocal.sessions[0].rollback_calls == 1
    assert "Execution failed." in result.output


def test_stage_profile_dry_run_shows_candidate_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    patch_staging_service(
        monkeypatch,
        make_result(
            dry_run=True,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            candidates=[make_candidate(index=index) for index in range(1, 53)],
        ),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12"])

    assert result.exit_code == 0
    visible_candidates = [
        line
        for line in result.output.splitlines()
        if line.startswith("Candidate ") and line.split(" ", 2)[1].isdigit()
    ]
    assert len(visible_candidates) == 50
    assert "Additional candidates not displayed: 2" in result.output


def test_stage_profile_does_not_call_legacy_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    patch_staging_service(
        monkeypatch,
        make_result(dry_run=False, status=CompanyDiscoveryRunStatus.SUCCEEDED),
    )
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(cli, "SearchProfileService", fake_profile_service_factory(make_profile()))
    monkeypatch.setattr(cli, "ProjectService", fake_project_service_factory(True))
    monkeypatch.setattr(cli, "SerpApiClient", FakeSerpApiClient)
    monkeypatch.setattr(
        cli,
        "SearchProfileDiscoveryPersistenceService",
        lambda *args: pytest.fail("Legacy persistence path invoked."),
    )
    monkeypatch.setattr(
        cli,
        "CompanyDiscoveryService",
        lambda *args, **kwargs: pytest.fail("Legacy company discovery path invoked."),
    )

    result = runner.invoke(cli.app, ["stage-profile", "--profile-id", "12", "--persist", "--yes"])

    assert result.exit_code == 0
    assert "Mode: PERSIST" in result.output


class _SpySession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.lookup_calls: list[str] = []

    def __getattribute__(self, name: str) -> Any:
        if name in (
            "commit",
            "rollback",
            "execute",
            "close",
            "flush",
            "__class__",
            "__dict__",
        ):
            object.__getattribute__(self, "lookup_calls").append(name)
        return object.__getattribute__(self, name)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


@pytest.mark.parametrize(
    "method",
    [
        "execute",
        "close",
        "flush",
        "__class__",
        "__dict__",
        "",
        " ",
        "COMMIT",
        None,
        True,
        7,
        object(),
    ],
)
def test_invoke_session_method_rejects_unsupported_operations(method: Any) -> None:
    session = _SpySession()

    with pytest.raises(ValueError, match="Unsupported session operation."):
        cli._invoke_session_method(cast(Session, session), method)

    assert session.lookup_calls == []
    assert session.commit_calls == 0
    assert session.rollback_calls == 0


def test_invoke_session_method_commits_once_and_does_not_rollback() -> None:
    session = _SpySession()

    cli._invoke_session_method(cast(Session, session), "commit")

    assert session.lookup_calls == ["commit"]
    assert session.commit_calls == 1
    assert session.rollback_calls == 0


def test_invoke_session_method_commit_exception_is_not_suppressed() -> None:
    class _RaisesSession(_SpySession):
        def commit(self) -> None:
            raise ValueError("commit failure")

    session = _RaisesSession()

    with pytest.raises(ValueError, match="commit failure"):
        cli._invoke_session_method(cast(Session, session), "commit")

    assert session.lookup_calls == ["commit"]
    assert session.rollback_calls == 0


class _CommitBaseException(BaseException):
    pass


def test_invoke_session_method_commit_base_exception_is_not_suppressed() -> None:
    class _RaisesSession(_SpySession):
        def commit(self) -> None:
            raise _CommitBaseException("base commit failure")

    session = _RaisesSession()

    with pytest.raises(_CommitBaseException, match="base commit failure"):
        cli._invoke_session_method(cast(Session, session), "commit")

    assert session.lookup_calls == ["commit"]
    assert session.rollback_calls == 0


def test_invoke_session_method_rollback_calls_exactly_once_and_does_not_commit() -> None:
    session = _SpySession()

    cli._invoke_session_method(cast(Session, session), "rollback")

    assert session.lookup_calls == ["rollback"]
    assert session.commit_calls == 0
    assert session.rollback_calls == 1


def test_invoke_session_method_rollback_exception_is_not_suppressed() -> None:
    class _RaisesSession(_SpySession):
        def rollback(self) -> None:
            raise ValueError("rollback failure")

    session = _RaisesSession()

    with pytest.raises(ValueError, match="rollback failure"):
        cli._invoke_session_method(cast(Session, session), "rollback")

    assert session.lookup_calls == ["rollback"]
    assert session.commit_calls == 0


class _RollbackBaseException(BaseException):
    pass


def test_invoke_session_method_rollback_base_exception_is_not_suppressed() -> None:
    class _RaisesSession(_SpySession):
        def rollback(self) -> None:
            raise _RollbackBaseException("base rollback failure")

    session = _RaisesSession()

    with pytest.raises(_RollbackBaseException, match="base rollback failure"):
        cli._invoke_session_method(cast(Session, session), "rollback")

    assert session.lookup_calls == ["rollback"]
    assert session.commit_calls == 0


def test_safe_rollback_calls_rollback_and_suppresses_exception() -> None:
    class _RaisesSession(_SpySession):
        def __init__(self) -> None:
            super().__init__()
            self.rollback_invoked = False

        def rollback(self) -> None:
            self.rollback_invoked = True
            raise ValueError("rollback failure")

    session = _RaisesSession()

    cli._safe_rollback(cast(Session, session))

    assert session.lookup_calls == ["rollback"]
    assert session.commit_calls == 0
    assert session.rollback_invoked is True


def test_safe_rollback_does_not_propagate_exception() -> None:
    class _RaisesSession(_SpySession):
        def rollback(self) -> None:
            raise _RollbackBaseException("rollback base failure")

    session = _RaisesSession()

    with pytest.raises(_RollbackBaseException, match="rollback base failure"):
        cli._safe_rollback(cast(Session, session))

    assert session.lookup_calls == ["rollback"]
    assert session.commit_calls == 0


def test_safe_rollback_calls_only_rollback_operation() -> None:
    captured_methods: list[str] = []

    def fake_invoke(session: Any, method: str) -> None:
        captured_methods.append(method)

    original_invoke = cli._invoke_session_method
    try:
        cli._invoke_session_method = fake_invoke
        session = _SpySession()
        cli._safe_rollback(cast(Session, session))
    finally:
        cli._invoke_session_method = original_invoke

    assert captured_methods == ["rollback"]

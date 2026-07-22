from datetime import UTC, datetime
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from app.cli import company_discovery_candidates as candidate_cli
from app.cli import main as cli_main
from app.modules.company_discovery import CompanyDiscoveryCandidateTransitionError
from app.modules.company_discovery.candidate_review import (
    CompanyDiscoveryCandidateReviewNotFoundError,
)
from app.modules.company_discovery.candidate_review_schemas import (
    CompanyDiscoveryCandidateReviewResult,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateRead

runner = CliRunner()
_DEFAULT_PROJECT_SERVICE_FACTORY = candidate_cli._make_project_service_from_repository
_DEFAULT_REPOSITORY_FACTORY = candidate_cli.CompanyDiscoveryStagingRepository  # type: ignore[attr-defined]


def make_candidate_read(
    *,
    candidate_id: int,
    project_id: int,
    status: CompanyDiscoveryCandidateStatus,
) -> CompanyDiscoveryCandidateRead:
    return CompanyDiscoveryCandidateRead(
        id=candidate_id,
        project_id=project_id,
        first_seen_run_id=1,
        last_seen_run_id=2,
        provider="serpapi",
        name="Acme",
        normalized_name="acme",
        website="https://www.acme.example",
        website_identity="www.acme.example",
        country_code="US",
        identity_key="website:www.acme.example",
        best_position=7,
        candidate_status=status,
        promoted_company_id=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def make_review_result(
    *,
    candidate_id: int,
    project_id: int,
    previous_status: CompanyDiscoveryCandidateStatus,
    current_status: CompanyDiscoveryCandidateStatus,
    changed: bool,
) -> CompanyDiscoveryCandidateReviewResult:
    return CompanyDiscoveryCandidateReviewResult(
        candidate=make_candidate_read(
            candidate_id=candidate_id,
            project_id=project_id,
            status=current_status,
        ),
        previous_status=previous_status,
        current_status=current_status,
        changed=changed,
    )


class FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


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


def make_session_local(
    *,
    enter_error: BaseException | None = None,
    exit_error: BaseException | None = None,
    commit_error: Exception | None = None,
    rollback_error: Exception | None = None,
) -> Any:
    class ControlledSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.commit_error = commit_error
            self.rollback_error = rollback_error

        def commit(self) -> None:
            self.commit_calls += 1
            if self.commit_error is not None:
                raise self.commit_error

        def rollback(self) -> None:
            self.rollback_calls += 1
            if self.rollback_error is not None:
                raise self.rollback_error

    class ControlledSessionLocal:
        calls = 0
        sessions: list[ControlledSession] = []

        def __init__(self) -> None:
            type(self).calls += 1
            self.session = ControlledSession()
            type(self).sessions.append(self.session)

        def __enter__(self) -> ControlledSession:
            if enter_error is not None:
                raise enter_error
            return self.session

        def __exit__(self, *_args: object) -> None:
            if exit_error is not None:
                raise exit_error
            return None

    return ControlledSessionLocal


def install_cli_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_local: Any | None = None,
    project_repository: object | None = None,
    service_factory: Any | None = None,
    repository_factory: Any | None = None,
    project_service_factory: Any | None = None,
) -> None:
    monkeypatch.setattr(
        candidate_cli,
        "SessionLocal",
        session_local or make_session_local(),
    )
    monkeypatch.setattr(
        candidate_cli,
        "ProjectRepository",
        project_repository or project_repository_factory(True),
    )
    monkeypatch.setattr(
        candidate_cli,
        "CompanyDiscoveryCandidateReviewService",
        service_factory or FakeReviewService,
    )
    if repository_factory is not None:
        monkeypatch.setattr(candidate_cli, "CompanyDiscoveryStagingRepository", repository_factory)
    else:
        monkeypatch.setattr(
            candidate_cli,
            "CompanyDiscoveryStagingRepository",
            _DEFAULT_REPOSITORY_FACTORY,
        )
    if project_service_factory is not None:
        monkeypatch.setattr(
            candidate_cli, "_make_project_service_from_repository", project_service_factory
        )
    else:
        monkeypatch.setattr(
            candidate_cli,
            "_make_project_service_from_repository",
            _DEFAULT_PROJECT_SERVICE_FACTORY,
        )


def assert_sanitized_dependency_failure(
    *,
    result: Any,
    message: str,
    marker: str,
) -> None:
    assert result.exit_code == 1
    assert message in result.output
    assert marker not in result.output
    assert "Traceback" not in result.output


def reset_fakes() -> None:
    FakeSessionLocal.calls = 0
    FakeSessionLocal.sessions = []


def project_repository_factory(project_exists: bool = True) -> type:
    class FakeProjectRepository:
        def __init__(self, repository: object) -> None:
            pass

        def get(self, project_id: int) -> object | None:
            if not project_exists or project_id != 12:
                return None
            return object()

    return FakeProjectRepository


class FakeReviewService:
    def __init__(self, repository: object) -> None:
        pass

    def get_candidate(self, project_id: int, candidate_id: int) -> CompanyDiscoveryCandidateRead:
        if project_id != 12 or candidate_id != 34:
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")
        return make_candidate_read(
            candidate_id=candidate_id,
            project_id=project_id,
            status=CompanyDiscoveryCandidateStatus.REVIEWED,
        )

    def list_candidates(
        self,
        project_id: int,
        limit: int,
        offset: int = 0,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> list[CompanyDiscoveryCandidateRead]:
        if project_id != 12:
            return []
        status = candidate_status or CompanyDiscoveryCandidateStatus.DISCOVERED
        return [
            make_candidate_read(candidate_id=34 + i, project_id=project_id, status=status)
            for i in range(3)
        ]

    def mark_reviewed(
        self, project_id: int, candidate_id: int
    ) -> CompanyDiscoveryCandidateReviewResult:
        if project_id != 12 or candidate_id != 34:
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")
        return make_review_result(
            candidate_id=candidate_id,
            project_id=project_id,
            previous_status=CompanyDiscoveryCandidateStatus.DISCOVERED,
            current_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            changed=True,
        )

    def reject(self, project_id: int, candidate_id: int) -> CompanyDiscoveryCandidateReviewResult:
        if project_id != 12 or candidate_id != 34:
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")
        return make_review_result(
            candidate_id=candidate_id,
            project_id=project_id,
            previous_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            current_status=CompanyDiscoveryCandidateStatus.REJECTED,
            changed=True,
        )


def test_candidate_group_is_registered_in_main_help() -> None:
    result = runner.invoke(cli_main.app, ["company-discovery", "--help"])
    assert result.exit_code == 0
    assert "candidate" in result.output


def test_candidate_subcommands_are_visible() -> None:
    result = runner.invoke(cli_main.app, ["company-discovery", "candidate", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "show" in result.output
    assert "review" in result.output
    assert "reject" in result.output


def test_list_candidate_output_is_safe_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    class BigListService(FakeReviewService):
        def list_candidates(
            self,
            project_id: int,
            limit: int,
            offset: int = 0,
            candidate_status: CompanyDiscoveryCandidateStatus | None = None,
        ) -> list[CompanyDiscoveryCandidateRead]:
            return [
                make_candidate_read(
                    candidate_id=100 + i,
                    project_id=12,
                    status=CompanyDiscoveryCandidateStatus.DISCOVERED,
                )
                for i in range(105)
            ]

    class BigListReviewService(BigListService):
        pass

    monkeypatch.setattr(
        candidate_cli, "CompanyDiscoveryCandidateReviewService", BigListReviewService
    )
    result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "list", "--project-id", "12", "--limit", "100"],
    )

    assert result.exit_code == 0
    assert result.output.count("Candidate ID: ") == 100
    assert "identity_key" not in result.output


def test_list_invalid_status_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "list", "--project-id", "12", "--status", "INVALID"],
    )
    assert result.exit_code == 1
    assert "Invalid candidate status." in result.output


def test_list_limits_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "list", "--project-id", "12", "--limit", "0"],
    )
    assert result.exit_code == 1
    assert "Invalid list limit. Limit must be between 1 and 100." in result.output


def test_review_requires_yes_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "review", "--project-id", "12", "--candidate-id", "34"],
    )
    assert result.exit_code == 1
    assert "Candidate status change requires --yes." in result.output
    assert FakeSessionLocal.calls == 0


def test_review_success_commits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert FakeSessionLocal.calls == 1
    assert FakeSessionLocal.sessions[0].commit_calls == 1
    assert FakeSessionLocal.sessions[0].rollback_calls == 0
    assert "Candidate ID: 34" in result.output
    assert "Changed: yes" in result.output


def test_reject_success_commits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "reject",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert FakeSessionLocal.calls == 1
    assert FakeSessionLocal.sessions[0].commit_calls == 1


def test_idempotent_review_is_successful_without_update(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))

    class IdempotentReviewService(FakeReviewService):
        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            return make_review_result(
                candidate_id=candidate_id,
                project_id=project_id,
                previous_status=CompanyDiscoveryCandidateStatus.REVIEWED,
                current_status=CompanyDiscoveryCandidateStatus.REVIEWED,
                changed=False,
            )

    monkeypatch.setattr(
        candidate_cli, "CompanyDiscoveryCandidateReviewService", IdempotentReviewService
    )

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Changed: no" in result.output


def test_forbidden_transition_uses_safe_message_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))

    class ForbiddenService:
        def __init__(self, repository: object) -> None:
            pass

        def get_candidate(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateRead:
            return make_candidate_read(
                candidate_id=candidate_id,
                project_id=project_id,
                status=CompanyDiscoveryCandidateStatus.REJECTED,
            )

        def list_candidates(
            self,
            project_id: int,
            limit: int,
            offset: int = 0,
            candidate_status: CompanyDiscoveryCandidateStatus | None = None,
        ) -> list[CompanyDiscoveryCandidateRead]:
            return []

        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise CompanyDiscoveryCandidateTransitionError(
                "Candidate status transition is not allowed."
            )

        def reject(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise CompanyDiscoveryCandidateTransitionError(
                "Candidate status transition is not allowed."
            )

    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", ForbiddenService)

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Candidate status transition is not allowed." in result.output
    assert FakeSessionLocal.sessions[0].rollback_calls == 1


def test_list_dependency_failures_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    install_cli_dependencies(monkeypatch)

    class SessionFactoryFailure:
        def __init__(self) -> None:
            raise RuntimeError("session_factory_fault")

        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class BrokenProjectRepository:
        def __init__(self, _session: object) -> None:
            raise RuntimeError("project_repository_fault")

    class BrokenProjectService:
        def __init__(self, _repository: object) -> None:
            raise RuntimeError("project_service_factory_fault")

    class BrokenProject:
        def get(self, _project_id: int) -> object:
            raise RuntimeError("project_lookup_fault")

    class BrokenCandidateRepository:
        def __init__(self, _session: object) -> None:
            raise RuntimeError("repository_factory_fault")

    class BrokenCandidateService:
        def __init__(self, _repository: object) -> None:
            raise RuntimeError("service_factory_fault")

    class BrokenCandidateServiceMethod(FakeReviewService):
        def list_candidates(
            self,
            project_id: int,
            limit: int,
            offset: int = 0,
            candidate_status: CompanyDiscoveryCandidateStatus | None = None,
        ) -> list[CompanyDiscoveryCandidateRead]:
            raise RuntimeError("service_list_fault")

    for boundary in [
        ("session_factory", SessionFactoryFailure),
        ("session_enter", make_session_local(enter_error=RuntimeError("session_enter_fault"))),
        ("project_repository", BrokenProjectRepository),
        ("project_service_factory", BrokenProjectService),
        ("project_lookup", BrokenProject),
        ("repository_factory", BrokenCandidateRepository),
        ("service_factory", BrokenCandidateService),
        ("service", BrokenCandidateServiceMethod),
        ("session_exit", make_session_local(exit_error=RuntimeError("session_exit_fault"))),
    ]:

        def _raise_project_service_factory(_: object) -> object:
            raise RuntimeError("project_service_factory_fault")

        label, patcher = boundary
        reset_fakes()
        install_cli_dependencies(monkeypatch)
        if label == "session_factory":
            monkeypatch.setattr(candidate_cli, "SessionLocal", SessionFactoryFailure)
        elif label == "session_enter":
            monkeypatch.setattr(candidate_cli, "SessionLocal", patcher)
        elif label == "project_repository":
            monkeypatch.setattr(candidate_cli, "ProjectRepository", patcher)
        elif label == "project_service_factory":
            monkeypatch.setattr(
                candidate_cli,
                "_make_project_service_from_repository",
                _raise_project_service_factory,
            )
        elif label == "project_lookup":
            monkeypatch.setattr(
                candidate_cli, "ProjectRepository", lambda _session: BrokenProject()
            )
        elif label == "repository_factory":
            monkeypatch.setattr(candidate_cli, "CompanyDiscoveryStagingRepository", patcher)
        elif label == "service_factory":
            monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", patcher)
        elif label == "service":
            monkeypatch.setattr(
                candidate_cli,
                "CompanyDiscoveryCandidateReviewService",
                BrokenCandidateServiceMethod,
            )
        elif label == "session_exit":
            monkeypatch.setattr(candidate_cli, "SessionLocal", patcher)
        result = runner.invoke(
            cli_main.app,
            ["company-discovery", "candidate", "list", "--project-id", "12"],
        )
        assert result.exit_code == 1
        assert "Candidate list failed." in result.output
        assert "session_factory_fault" not in result.output
        assert "project_repository_fault" not in result.output
        assert "project_service_factory_fault" not in result.output
        assert "project_lookup_fault" not in result.output
        assert "repository_factory_fault" not in result.output
        assert "service_factory_fault" not in result.output
        assert "service_list_fault" not in result.output
        assert "session_enter_fault" not in result.output
        assert "session_exit_fault" not in result.output
        assert "Traceback" not in result.output


def test_show_dependency_failures_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenService:
        def __init__(self, _repository: object) -> None:
            raise RuntimeError("service_factory_fault")

    install_cli_dependencies(monkeypatch)
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", BrokenService)
    result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "show", "--project-id", "12", "--candidate-id", "34"],
    )
    assert_sanitized_dependency_failure(
        result=result,
        message="Candidate show failed.",
        marker="service_factory_fault",
    )


def test_review_dependency_failures_are_sanitized_and_rollback_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SessionFactoryFailure:
        def __init__(self) -> None:
            raise RuntimeError("session_factory_fault")

        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class BrokenProjectService:
        def __init__(self, _repository: object) -> None:
            raise RuntimeError("project_service_factory_fault")

    class BrokenProject:
        def get(self, _project_id: int) -> object:
            raise RuntimeError("project_lookup_fault")

    class BrokenRepository:
        def __init__(self, _session: object) -> None:
            raise RuntimeError("repository_factory_fault")

    class BrokenService:
        def __init__(self, _repository: object) -> None:
            raise RuntimeError("service_factory_fault")

        def get_candidate(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateRead:
            raise RuntimeError("UNUSED")

        def list_candidates(
            self,
            project_id: int,
            limit: int,
            offset: int = 0,
            candidate_status: CompanyDiscoveryCandidateStatus | None = None,
        ) -> list[CompanyDiscoveryCandidateRead]:
            return []

        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise RuntimeError("service_review_fault")

        def reject(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise RuntimeError("service_reject_fault")

    class CommitRollbackService(FakeReviewService):
        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            return make_review_result(
                candidate_id=candidate_id,
                project_id=project_id,
                previous_status=CompanyDiscoveryCandidateStatus.DISCOVERED,
                current_status=CompanyDiscoveryCandidateStatus.REVIEWED,
                changed=True,
            )

    class ServiceThatRollbacksThenRaises:
        def __init__(self, repository: Any) -> None:
            self.repository = repository

        def get_candidate(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateRead:
            return make_candidate_read(
                candidate_id=candidate_id,
                project_id=project_id,
                status=CompanyDiscoveryCandidateStatus.DISCOVERED,
            )

        def list_candidates(
            self,
            project_id: int,
            limit: int,
            offset: int = 0,
            candidate_status: CompanyDiscoveryCandidateStatus | None = None,
        ) -> list[CompanyDiscoveryCandidateRead]:
            return []

        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            self.repository.session.rollback()
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")

        def reject(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise CompanyDiscoveryCandidateReviewNotFoundError("Candidate was not found.")

    class RepoWithSession:
        def __init__(self, session: object) -> None:
            self.session = session

    class ServiceMethodFailure(CommitRollbackService):
        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise RuntimeError("service_review_fault")

    for transition, patcher in [
        ("session_factory", SessionFactoryFailure),
        ("project_lookup", BrokenProject),
        ("project_service_factory", BrokenProjectService),
        ("repository_factory", BrokenRepository),
        ("service_factory", BrokenService),
        ("service", ServiceMethodFailure),
    ]:

        def _raise_project_service_factory(_: object) -> object:
            raise RuntimeError("project_service_factory_fault")

        reset_fakes()
        install_cli_dependencies(monkeypatch)
        if transition == "session_factory":
            monkeypatch.setattr(candidate_cli, "SessionLocal", patcher)
        elif transition == "project_lookup":
            monkeypatch.setattr(
                candidate_cli, "ProjectRepository", lambda _session: BrokenProject()
            )
        elif transition == "project_service_factory":
            monkeypatch.setattr(
                candidate_cli,
                "_make_project_service_from_repository",
                _raise_project_service_factory,
            )
        elif transition == "repository_factory":
            monkeypatch.setattr(
                candidate_cli, "CompanyDiscoveryStagingRepository", BrokenRepository
            )
        elif transition == "service_factory":
            monkeypatch.setattr(
                candidate_cli, "CompanyDiscoveryCandidateReviewService", BrokenService
            )
        elif transition == "service":
            monkeypatch.setattr(
                candidate_cli, "CompanyDiscoveryCandidateReviewService", ServiceMethodFailure
            )

        result = runner.invoke(
            cli_main.app,
            [
                "company-discovery",
                "candidate",
                "review",
                "--project-id",
                "12",
                "--candidate-id",
                "34",
                "--yes",
            ],
        )
        assert result.exit_code == 1
        assert result.output.count("Candidate status update failed.") == 1
        assert "Traceback" not in result.output
        assert "service_review_fault" not in result.output
        assert "service_reject_fault" not in result.output
        assert "session_factory_fault" not in result.output
        assert "project_service_factory_fault" not in result.output
        assert "project_lookup_fault" not in result.output
        assert "repository_factory_fault" not in result.output
        assert "service_factory_fault" not in result.output

    session_local = make_session_local()
    install_cli_dependencies(
        monkeypatch, session_local=session_local, service_factory=CommitRollbackService
    )
    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert session_local.sessions[0].commit_calls == 1
    assert session_local.sessions[0].rollback_calls == 0

    session_local = make_session_local(commit_error=RuntimeError("commit_failure"))
    install_cli_dependencies(
        monkeypatch, session_local=session_local, service_factory=CommitRollbackService
    )
    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Candidate status update failed." in result.output
    assert session_local.sessions[0].commit_calls == 1
    assert session_local.sessions[0].rollback_calls == 1

    session_local = make_session_local(exit_error=RuntimeError("session_exit_failure"))
    install_cli_dependencies(
        monkeypatch, session_local=session_local, service_factory=CommitRollbackService
    )
    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Candidate status update failed." in result.output
    assert session_local.sessions[0].commit_calls == 1
    assert session_local.sessions[0].rollback_calls == 0

    session_local = make_session_local()
    install_cli_dependencies(
        monkeypatch,
        session_local=session_local,
        repository_factory=RepoWithSession,
        service_factory=ServiceThatRollbacksThenRaises,
    )
    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Candidate status update failed." in result.output
    assert session_local.sessions[0].rollback_calls == 1


def test_review_baseexception_boundary_path_is_not_sanitized() -> None:
    with pytest.raises(KeyboardInterrupt):
        candidate_cli.execute_review_candidate(
            project_id=12,
            candidate_id=34,
            yes=True,
            transition="review",
            session_factory=make_session_local(enter_error=KeyboardInterrupt("boom")),
            project_service_factory=lambda _: (_ for _ in ()).throw(KeyboardInterrupt("boom")),
            repository_factory=cast(Any, FakeReviewService),
            service_factory=cast(Any, FakeReviewService),
        )


def test_list_baseexception_boundary_path_is_not_sanitized() -> None:
    with pytest.raises(KeyboardInterrupt):
        candidate_cli.execute_list_candidates(
            project_id=12,
            status=None,
            limit=50,
            offset=0,
            session_factory=make_session_local(enter_error=KeyboardInterrupt("boom")),
        )


def test_show_baseexception_boundary_path_is_not_sanitized() -> None:
    with pytest.raises(KeyboardInterrupt):
        candidate_cli.execute_show_candidate(
            project_id=12,
            candidate_id=34,
            session_factory=make_session_local(enter_error=KeyboardInterrupt("boom")),
        )


def test_invalid_transition_does_not_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    install_cli_dependencies(monkeypatch)
    for transition in ["promote", "discovered", "", " ", True, 1, object()]:
        session_local = make_session_local()
        monkeypatch.setattr(candidate_cli, "SessionLocal", session_local)
        result = candidate_cli.execute_review_candidate(
            project_id=12,
            candidate_id=34,
            yes=True,
            transition=transition,  # type: ignore[arg-type]
        )
        assert result.exit_code == 1
        assert result.error_message == "Candidate status update failed."
        assert session_local.sessions[0].commit_calls == 0
        assert session_local.sessions[0].rollback_calls == 0


def test_service_failure_rolls_back_and_hides_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))

    class FailingService:
        def __init__(self, repository: object) -> None:
            pass

        def get_candidate(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateRead:
            return make_candidate_read(
                candidate_id=candidate_id,
                project_id=project_id,
                status=CompanyDiscoveryCandidateStatus.DISCOVERED,
            )

        def list_candidates(
            self,
            project_id: int,
            limit: int,
            offset: int = 0,
            candidate_status: CompanyDiscoveryCandidateStatus | None = None,
        ) -> list[CompanyDiscoveryCandidateRead]:
            return []

        def mark_reviewed(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise RuntimeError("critical failure")

        def reject(
            self, project_id: int, candidate_id: int
        ) -> CompanyDiscoveryCandidateReviewResult:
            raise RuntimeError("critical failure")

    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FailingService)

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "Candidate status update failed." in result.output
    assert "critical failure" not in result.output
    assert FakeSessionLocal.sessions[0].rollback_calls == 1


def test_commit_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    class CommitFailSession(FakeSession):
        def commit(self) -> None:
            raise RuntimeError("commit failed")

    class CommitFailSessionLocal:
        calls = 0

        def __init__(self) -> None:
            type(self).calls += 1
            self.session = CommitFailSession()

        def __enter__(self) -> CommitFailSession:
            return self.session

        def __exit__(self, *args: object) -> None:
            return None

    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", CommitFailSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "review",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Candidate status update failed." in result.output


def test_list_and_show_do_not_print_identity_key_or_raw_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    list_result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "list", "--project-id", "12"],
    )
    show_result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "show", "--project-id", "12", "--candidate-id", "34"],
    )

    for output in (list_result.output, show_result.output):
        assert "Identity Key" not in output
        assert "website_identity" not in output
        assert "notes" not in output
        assert "snippets" not in output


def test_promotion_command_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(cli_main.app, ["company-discovery", "candidate", "--help"])
    assert "promote" in result.output.casefold()

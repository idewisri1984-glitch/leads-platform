from datetime import UTC, datetime

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


def test_no_promotion_command_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_fakes()
    monkeypatch.setattr(candidate_cli, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", project_repository_factory(True))
    monkeypatch.setattr(candidate_cli, "CompanyDiscoveryCandidateReviewService", FakeReviewService)

    result = runner.invoke(cli_main.app, ["company-discovery", "candidate", "--help"])
    assert "promote" not in result.output.casefold()

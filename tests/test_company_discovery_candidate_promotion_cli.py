from typing import Any, cast

import pytest
from typer.testing import CliRunner

from app.cli import company_discovery_candidates as candidate_cli
from app.cli import main as cli_main
from app.modules.company_discovery import (
    CompanyDiscoveryCandidateNotEligibleError,
    CompanyDiscoveryCandidatePromotionConsistencyError,
    CompanyDiscoveryCandidatePromotionInvalidDataError,
    CompanyDiscoveryCandidatePromotionNotFoundError,
)
from app.modules.company_discovery.candidate_promotion_schemas import (
    CompanyDiscoveryCandidatePromotionResult,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus

runner = CliRunner()


def promotion_result(
    *,
    created: bool = True,
    changed: bool = True,
    company_id: int = 91,
) -> CompanyDiscoveryCandidatePromotionResult:
    previous = (
        CompanyDiscoveryCandidateStatus.REVIEWED
        if changed
        else CompanyDiscoveryCandidateStatus.PROMOTED
    )
    return CompanyDiscoveryCandidatePromotionResult(
        candidate_id=34,
        project_id=12,
        company_id=company_id,
        previous_status=previous,
        current_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        created_company=created,
        changed=changed,
    )


class ControlledSession:
    def __init__(
        self,
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error


def make_session_factory(
    *,
    enter_error: BaseException | None = None,
    exit_error: BaseException | None = None,
    commit_error: BaseException | None = None,
    rollback_error: BaseException | None = None,
) -> Any:
    class SessionFactory:
        calls = 0
        sessions: list[ControlledSession] = []

        def __init__(self) -> None:
            type(self).calls += 1
            self.session = ControlledSession(
                commit_error=commit_error,
                rollback_error=rollback_error,
            )
            type(self).sessions.append(self.session)

        def __enter__(self) -> ControlledSession:
            if enter_error is not None:
                raise enter_error
            return self.session

        def __exit__(self, *_args: object) -> None:
            if exit_error is not None:
                raise exit_error
            return None

    return SessionFactory


class ProjectService:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists

    def get(self, project_id: int) -> object | None:
        return object() if self.exists and project_id == 12 else None


class PromotionService:
    def __init__(
        self,
        result: CompanyDiscoveryCandidatePromotionResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or promotion_result()
        self.error = error
        self.calls: list[tuple[int, int]] = []

    def promote(
        self, project_id: int, candidate_id: int
    ) -> CompanyDiscoveryCandidatePromotionResult:
        self.calls.append((project_id, candidate_id))
        if self.error is not None:
            raise self.error
        return self.result


def execute_with(
    service: PromotionService,
    *,
    session_factory: Any | None = None,
    project_exists: bool = True,
) -> tuple[candidate_cli.CandidatePromotionCommandOutcome, Any]:
    factory = session_factory or make_session_factory()
    outcome = candidate_cli.execute_promote_candidate(
        project_id=12,
        candidate_id=34,
        yes=True,
        session_factory=factory,
        project_service_factory=cast(Any, lambda _repository: ProjectService(project_exists)),
        staging_repository_factory=cast(Any, lambda _session: object()),
        company_repository_factory=cast(Any, lambda _session: object()),
        promotion_service_factory=cast(Any, lambda _staging, _company: service),
    )
    return outcome, factory


def install_command_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service: PromotionService,
    session_factory: Any,
) -> None:
    monkeypatch.setattr(candidate_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(candidate_cli, "ProjectRepository", lambda _session: object())
    monkeypatch.setattr(
        candidate_cli,
        "_make_project_service_from_repository",
        lambda _repository: ProjectService(),
    )
    monkeypatch.setattr(
        candidate_cli, "CompanyDiscoveryStagingRepository", lambda _session: object()
    )
    monkeypatch.setattr(candidate_cli, "CompanyRepository", lambda _session: object())
    monkeypatch.setattr(
        candidate_cli,
        "CompanyDiscoveryCandidatePromotionService",
        lambda _staging, _company: service,
    )


def test_promote_appears_in_candidate_help() -> None:
    result = runner.invoke(cli_main.app, ["company-discovery", "candidate", "--help"])
    assert result.exit_code == 0
    assert "promote" in result.output


def test_yes_is_required_before_session_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = make_session_factory()
    monkeypatch.setattr(candidate_cli, "SessionLocal", factory)

    result = runner.invoke(
        cli_main.app,
        ["company-discovery", "candidate", "promote", "--project-id", "12", "--candidate-id", "34"],
    )

    assert result.exit_code == 1
    assert "Candidate promotion requires --yes." in result.output
    assert factory.calls == 0


@pytest.mark.parametrize(
    "expected",
    [
        promotion_result(created=True, changed=True, company_id=91),
        promotion_result(created=False, changed=True, company_id=77),
        promotion_result(created=False, changed=False, company_id=77),
    ],
)
def test_creation_reuse_and_idempotent_paths_commit_once(
    expected: CompanyDiscoveryCandidatePromotionResult,
) -> None:
    service = PromotionService(result=expected)
    outcome, factory = execute_with(service)

    assert outcome.exit_code == 0
    assert outcome.result == expected
    assert service.calls == [(12, 34)]
    assert factory.sessions[0].commit_calls == 1
    assert factory.sessions[0].rollback_calls == 0


def test_success_output_is_fixed_and_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    service = PromotionService(result=promotion_result())
    factory = make_session_factory()
    install_command_dependencies(monkeypatch, service=service, session_factory=factory)

    result = runner.invoke(
        cli_main.app,
        [
            "company-discovery",
            "candidate",
            "promote",
            "--project-id",
            "12",
            "--candidate-id",
            "34",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "Candidate ID: 34",
        "Project ID: 12",
        "Company ID: 91",
        "Previous Status: REVIEWED",
        "Current Status: PROMOTED",
        "Company Created: yes",
        "Changed: yes",
    ]
    for forbidden in ["website", "identity", "provider", "Traceback", "API"]:
        assert forbidden.casefold() not in result.output.casefold()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (CompanyDiscoveryCandidatePromotionNotFoundError("private"), "Candidate was not found."),
        (
            CompanyDiscoveryCandidateNotEligibleError("private"),
            "Candidate is not eligible for promotion.",
        ),
        (
            CompanyDiscoveryCandidatePromotionInvalidDataError("private"),
            "Candidate promotion data is invalid.",
        ),
        (
            CompanyDiscoveryCandidatePromotionConsistencyError("private"),
            "Candidate promotion state is inconsistent.",
        ),
        (RuntimeError("private_raw_database_error"), "Candidate promotion failed."),
    ],
)
def test_failures_are_mapped_safely_and_rollback_once(
    error: Exception,
    message: str,
) -> None:
    outcome, factory = execute_with(PromotionService(error=error))

    assert outcome.exit_code == 1
    assert outcome.error_message == message
    assert "private" not in outcome.error_message
    assert factory.sessions[0].commit_calls == 0
    assert factory.sessions[0].rollback_calls == 1


def test_invalid_ids_create_no_session() -> None:
    factory = make_session_factory()
    outcome = candidate_cli.execute_promote_candidate(
        project_id=0,
        candidate_id=34,
        yes=True,
        session_factory=factory,
    )
    assert outcome.error_message == "Candidate promotion data is invalid."
    assert factory.calls == 0


def test_missing_project_creates_no_repositories_and_does_not_commit() -> None:
    service = PromotionService()
    outcome, factory = execute_with(service, project_exists=False)
    assert outcome.error_message == "Candidate was not found."
    assert service.calls == []
    assert factory.sessions[0].commit_calls == 0
    assert factory.sessions[0].rollback_calls == 0


def test_factory_failure_is_safe_and_rolls_back_once() -> None:
    factory = make_session_factory()

    def fail_repository(_session: object) -> object:
        raise RuntimeError("repository_secret")

    outcome = candidate_cli.execute_promote_candidate(
        project_id=12,
        candidate_id=34,
        yes=True,
        session_factory=factory,
        project_service_factory=cast(Any, lambda _repository: ProjectService()),
        staging_repository_factory=cast(Any, fail_repository),
    )

    assert outcome.error_message == "Candidate promotion failed."
    assert "repository_secret" not in outcome.error_message
    assert factory.sessions[0].rollback_calls == 1


def test_commit_failure_attempts_one_commit_and_one_rollback() -> None:
    factory = make_session_factory(commit_error=RuntimeError("commit_secret"))
    outcome, _ = execute_with(PromotionService(), session_factory=factory)
    assert outcome.error_message == "Candidate promotion failed."
    assert factory.sessions[0].commit_calls == 1
    assert factory.sessions[0].rollback_calls == 1


def test_ordinary_rollback_failure_is_suppressed() -> None:
    factory = make_session_factory(rollback_error=RuntimeError("rollback_secret"))
    outcome, _ = execute_with(
        PromotionService(error=RuntimeError("service_secret")),
        session_factory=factory,
    )
    assert outcome.error_message == "Candidate promotion failed."
    assert factory.sessions[0].rollback_calls == 1


def test_session_exit_failure_after_commit_does_not_rollback() -> None:
    factory = make_session_factory(exit_error=RuntimeError("exit_secret"))
    outcome, _ = execute_with(PromotionService(), session_factory=factory)
    assert outcome.error_message == "Candidate promotion failed."
    assert factory.sessions[0].commit_calls == 1
    assert factory.sessions[0].rollback_calls == 0


def test_baseexception_and_rollback_baseexception_propagate() -> None:
    with pytest.raises(KeyboardInterrupt):
        execute_with(PromotionService(error=KeyboardInterrupt("critical")))

    factory = make_session_factory(rollback_error=KeyboardInterrupt("rollback critical"))
    with pytest.raises(KeyboardInterrupt):
        execute_with(PromotionService(error=RuntimeError("ordinary")), session_factory=factory)

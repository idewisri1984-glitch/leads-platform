from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import typer
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company_discovery import (
    CompanyDiscoveryCandidateReviewNotFoundError,
    CompanyDiscoveryCandidateReviewResult,
    CompanyDiscoveryCandidateReviewService,
    CompanyDiscoveryCandidateTransitionError,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateRead
from app.modules.project import ProjectRepository

app = typer.Typer(help="Manual candidate review and reject workflow.")

SessionFactory = Callable[[], Session]
RepositoryFactory = Callable[[Session], CompanyDiscoveryStagingRepository]
ServiceFactory = Callable[
    [CompanyDiscoveryStagingRepository], CompanyDiscoveryCandidateReviewService
]
ProjectServiceFactory = Callable[[ProjectRepository], "ProjectServiceProtocol"]

_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 50

INVALID_PROJECT_ID_ERROR = "Invalid project ID."
INVALID_ID_ERROR = "Invalid ID."
CANDIDATE_LIST_FAILED_ERROR = "Candidate list failed."
CANDIDATE_SHOW_FAILED_ERROR = "Candidate show failed."
CANDIDATE_STATUS_UPDATE_FAILED_ERROR = "Candidate status update failed."


class ProjectServiceProtocol(Protocol):
    def get(self, project_id: int) -> object | None: ...


class _ProjectServiceFromRepository:
    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def get(self, project_id: int) -> object | None:
        return self._repository.get(project_id)


@dataclass(frozen=True)
class CandidateReviewCommandOutcome:
    exit_code: int
    result: (
        CompanyDiscoveryCandidateReviewResult
        | CompanyDiscoveryCandidateRead
        | list[CompanyDiscoveryCandidateRead]
        | None
    ) = None
    error_message: str | None = None


@app.command("list")
def list_candidates(
    project_id: int = typer.Option(help="Project ID.", min=1),
    status: str | None = typer.Option(None, help="Optional status filter."),
    limit: int = typer.Option(_DEFAULT_LIST_LIMIT, help="Maximum candidate rows."),
    offset: int = typer.Option(0, help="Result offset for listing."),
) -> None:
    """List project-scoped candidates for manual review."""
    outcome = execute_list_candidates(
        project_id=project_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    elif isinstance(outcome.result, list):
        _print_candidate_list(outcome.result)
    raise typer.Exit(outcome.exit_code)


@app.command("show")
def show_candidate(
    project_id: int = typer.Option(help="Project ID.", min=1),
    candidate_id: int = typer.Option(help="Candidate ID.", min=1),
) -> None:
    """Show one project-scoped candidate."""
    outcome = execute_show_candidate(project_id=project_id, candidate_id=candidate_id)
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    elif isinstance(outcome.result, CompanyDiscoveryCandidateRead):
        _print_candidate(outcome.result)
    raise typer.Exit(outcome.exit_code)


@app.command("review")
def review_candidate(
    project_id: int = typer.Option(help="Project ID.", min=1),
    candidate_id: int = typer.Option(help="Candidate ID.", min=1),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm the requested candidate status change.",
    ),
) -> None:
    """Mark one candidate as REVIEWED."""
    outcome = execute_review_candidate(
        project_id=project_id,
        candidate_id=candidate_id,
        yes=yes,
        transition="review",
    )
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    elif isinstance(outcome.result, CompanyDiscoveryCandidateReviewResult):
        _print_review_result(outcome.result)
    raise typer.Exit(outcome.exit_code)


@app.command("reject")
def reject_candidate(
    project_id: int = typer.Option(help="Project ID.", min=1),
    candidate_id: int = typer.Option(help="Candidate ID.", min=1),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm the requested candidate status change.",
    ),
) -> None:
    """Mark one candidate as REJECTED."""
    outcome = execute_review_candidate(
        project_id=project_id,
        candidate_id=candidate_id,
        yes=yes,
        transition="reject",
    )
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    elif isinstance(outcome.result, CompanyDiscoveryCandidateReviewResult):
        _print_review_result(outcome.result)
    raise typer.Exit(outcome.exit_code)


def execute_list_candidates(
    *,
    project_id: int,
    status: str | None,
    limit: int,
    offset: int = 0,
    session_factory: SessionFactory | None = None,
    project_service_factory: ProjectServiceFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    service_factory: Callable[
        [CompanyDiscoveryStagingRepository], CompanyDiscoveryCandidateReviewService
    ]
    | None = None,
) -> CandidateReviewCommandOutcome:
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        return CandidateReviewCommandOutcome(exit_code=1, error_message=INVALID_PROJECT_ID_ERROR)

    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > _MAX_LIST_LIMIT
    ):
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message="Invalid list limit. Limit must be between 1 and 100.",
        )
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return CandidateReviewCommandOutcome(exit_code=1, error_message="Invalid list offset.")

    candidate_status: CompanyDiscoveryCandidateStatus | None = None
    if status is not None:
        try:
            candidate_status = CompanyDiscoveryCandidateStatus(status)
        except ValueError:
            return CandidateReviewCommandOutcome(
                exit_code=1,
                error_message="Invalid candidate status.",
            )

    make_session = session_factory or SessionLocal
    make_project_service = project_service_factory or _make_project_service_from_repository
    make_repository = repository_factory or CompanyDiscoveryStagingRepository
    make_service = service_factory or CompanyDiscoveryCandidateReviewService

    try:
        with make_session() as session:
            project_service = make_project_service(ProjectRepository(session))
            project = project_service.get(project_id)
            if project is None:
                return CandidateReviewCommandOutcome(
                    exit_code=1,
                    error_message="Project was not found.",
                )

            repository = make_repository(session)
            service = make_service(repository)
            candidates = service.list_candidates(
                project_id=project_id,
                limit=limit,
                offset=offset,
                candidate_status=candidate_status,
            )
    except Exception:
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message=CANDIDATE_LIST_FAILED_ERROR,
        )

    return CandidateReviewCommandOutcome(exit_code=0, result=candidates)


def execute_show_candidate(
    *,
    project_id: int,
    candidate_id: int,
    session_factory: SessionFactory | None = None,
    project_service_factory: ProjectServiceFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    service_factory: Callable[
        [CompanyDiscoveryStagingRepository], CompanyDiscoveryCandidateReviewService
    ]
    | None = None,
) -> CandidateReviewCommandOutcome:
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or project_id <= 0
        or isinstance(candidate_id, bool)
        or not isinstance(candidate_id, int)
        or candidate_id <= 0
    ):
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message=INVALID_ID_ERROR,
        )

    make_session = session_factory or SessionLocal
    make_project_service = project_service_factory or _make_project_service_from_repository
    make_repository = repository_factory or CompanyDiscoveryStagingRepository
    make_service = service_factory or CompanyDiscoveryCandidateReviewService

    try:
        with make_session() as session:
            project_service = make_project_service(ProjectRepository(session))
            project = project_service.get(project_id)
            if project is None:
                return CandidateReviewCommandOutcome(
                    exit_code=1,
                    error_message="Project was not found.",
                )

            repository = make_repository(session)
            service = make_service(repository)
            candidate = service.get_candidate(project_id, candidate_id)
    except CompanyDiscoveryCandidateReviewNotFoundError:
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message="Candidate was not found.",
        )
    except Exception:
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message=CANDIDATE_SHOW_FAILED_ERROR,
        )

    return CandidateReviewCommandOutcome(exit_code=0, result=candidate)


def execute_review_candidate(
    *,
    project_id: int,
    candidate_id: int,
    yes: bool,
    transition: Literal["review", "reject"],
    session_factory: SessionFactory | None = None,
    project_service_factory: ProjectServiceFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    service_factory: Callable[
        [CompanyDiscoveryStagingRepository], CompanyDiscoveryCandidateReviewService
    ]
    | None = None,
) -> CandidateReviewCommandOutcome:
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or project_id <= 0
        or isinstance(candidate_id, bool)
        or not isinstance(candidate_id, int)
        or candidate_id <= 0
    ):
        return CandidateReviewCommandOutcome(exit_code=1, error_message=INVALID_ID_ERROR)

    if not yes:
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message="Candidate status change requires --yes.",
        )

    make_session = session_factory or SessionLocal
    make_project_service = project_service_factory or _make_project_service_from_repository
    make_repository = repository_factory or CompanyDiscoveryStagingRepository
    make_service = service_factory or CompanyDiscoveryCandidateReviewService

    session: Session | None = None
    rollback_attempted = False
    committed = False

    try:
        with make_session() as session:
            project_service = make_project_service(ProjectRepository(session))
            project = project_service.get(project_id)
            if project is None:
                return CandidateReviewCommandOutcome(
                    exit_code=1,
                    error_message="Project was not found.",
                )

            repository = make_repository(session)
            service = make_service(repository)
            if transition == "review":
                result = service.mark_reviewed(project_id, candidate_id)
            elif transition == "reject":
                result = service.reject(project_id, candidate_id)
            else:
                return CandidateReviewCommandOutcome(
                    exit_code=1,
                    error_message=CANDIDATE_STATUS_UPDATE_FAILED_ERROR,
                )

            _invoke_session_method(session, "commit")
            committed = True

    except CompanyDiscoveryCandidateReviewNotFoundError:
        rollback_attempted = _safe_rollback_if_needed(
            session=session,
            rollback_attempted=rollback_attempted,
        )
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message=CANDIDATE_STATUS_UPDATE_FAILED_ERROR,
        )
    except CompanyDiscoveryCandidateTransitionError:
        rollback_attempted = _safe_rollback_if_needed(
            session=session,
            rollback_attempted=rollback_attempted,
        )
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message="Candidate status transition is not allowed.",
        )
    except Exception:
        if not committed:
            rollback_attempted = _safe_rollback_if_needed(
                session=session,
                rollback_attempted=rollback_attempted,
            )
        return CandidateReviewCommandOutcome(
            exit_code=1,
            error_message=CANDIDATE_STATUS_UPDATE_FAILED_ERROR,
        )

    return CandidateReviewCommandOutcome(exit_code=0, result=result)


def _safe_rollback_if_needed(
    *,
    session: Session | None,
    rollback_attempted: bool,
) -> bool:
    if rollback_attempted:
        return True
    if session is None:
        return False
    preexisting = getattr(session, "rollback_calls", 0)
    if isinstance(preexisting, int) and preexisting > 0:
        return True
    candidate_session = cast(Any, session)
    if getattr(candidate_session, "_candidate_cli_rollback_attempted", False):
        return True
    _safe_rollback(session)
    candidate_session._candidate_cli_rollback_attempted = True
    return True


def _invoke_session_method(session: Session, method: str) -> None:
    if not isinstance(method, str) or method not in ("commit", "rollback"):
        raise ValueError("Unsupported session operation.")

    callback = getattr(session, method, None)
    if not callable(callback):
        raise TypeError("Session operation is not callable.")

    callback()


def _safe_rollback(session: Session) -> None:
    with suppress(Exception):
        _invoke_session_method(session, "rollback")


def _make_project_service_from_repository(
    project_repository: ProjectRepository,
) -> ProjectServiceProtocol:
    return _ProjectServiceFromRepository(project_repository)


def _print_candidate_list(candidates: list[CompanyDiscoveryCandidateRead]) -> None:
    for candidate in candidates[:_MAX_LIST_LIMIT]:
        _print_candidate(candidate)


def _print_candidate(candidate: CompanyDiscoveryCandidateRead) -> None:
    typer.echo(f"Candidate ID: {candidate.id}")
    typer.echo(f"Project ID: {candidate.project_id}")
    typer.echo(f"Provider: {candidate.provider}")
    typer.echo(f"Name: {candidate.name or ''}")
    typer.echo(f"Normalized Website: {candidate.website or ''}")
    typer.echo(f"Country Code: {candidate.country_code or ''}")
    typer.echo(f"Best Position: {candidate.best_position or ''}")
    typer.echo(f"Status: {candidate.candidate_status.value}")
    typer.echo(f"First Seen Run ID: {candidate.first_seen_run_id}")
    typer.echo(f"Last Seen Run ID: {candidate.last_seen_run_id}")
    if candidate.promoted_company_id is not None:
        typer.echo(f"Promoted Company ID: {candidate.promoted_company_id}")


def _print_review_result(result: CompanyDiscoveryCandidateReviewResult) -> None:
    typer.echo(f"Candidate ID: {result.candidate.id}")
    typer.echo(f"Previous Status: {result.previous_status.value}")
    typer.echo(f"Current Status: {result.current_status.value}")
    typer.echo(f"Changed: {'yes' if result.changed else 'no'}")

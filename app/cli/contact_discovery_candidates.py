from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import typer
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact_discovery import (
    ContactDiscoveryCandidateRead,
    ContactDiscoveryCandidateReviewNotFoundError,
    ContactDiscoveryCandidateReviewResult,
    ContactDiscoveryCandidateReviewService,
    ContactDiscoveryCandidateStatus,
    ContactDiscoveryCandidateTransitionError,
    ContactDiscoveryRepository,
)

app = typer.Typer(help="Manual contact candidate review and rejection.")

SessionFactory = Callable[[], Session]
CompanyRepositoryFactory = Callable[[Session], CompanyRepository]
RepositoryFactory = Callable[[Session], ContactDiscoveryRepository]
ServiceFactory = Callable[[ContactDiscoveryRepository], ContactDiscoveryCandidateReviewService]

_MAX_LIST_LIMIT = 100
_MAX_DISPLAY_LENGTH = 160
_INVALID_COMPANY_ID = "Invalid company ID."
_INVALID_ID = "Invalid ID."
_INVALID_STATUS = "Invalid candidate status."
_INVALID_LIMIT = "Invalid list limit."
_INVALID_OFFSET = "Invalid list offset."
_COMPANY_NOT_FOUND = "Company was not found."
_CANDIDATE_NOT_FOUND = "Candidate was not found."
_TRANSITION_FORBIDDEN = "Candidate status transition is not allowed."
_CONFIRMATION_REQUIRED = "Candidate status change requires --yes."
_LIST_FAILED = "Candidate list failed."
_SHOW_FAILED = "Candidate show failed."
_UPDATE_FAILED = "Candidate status update failed."


@dataclass(frozen=True)
class CandidateCommandOutcome:
    exit_code: int
    result: (
        ContactDiscoveryCandidateReviewResult
        | ContactDiscoveryCandidateRead
        | list[ContactDiscoveryCandidateRead]
        | None
    ) = None
    error_message: str | None = None


@app.command("list")
def list_candidates(
    company_id: int = typer.Option(help="Company ID.", min=1),
    status: str | None = typer.Option(None, help="Optional exact status filter."),
    limit: int = typer.Option(50, help="Maximum rows."),
    offset: int = typer.Option(0, help="Result offset."),
) -> None:
    outcome = execute_list_candidates(
        company_id=company_id, status=status, limit=limit, offset=offset
    )
    _emit(outcome)


@app.command("show")
def show_candidate(
    company_id: int = typer.Option(help="Company ID.", min=1),
    candidate_id: int = typer.Option(help="Candidate ID.", min=1),
) -> None:
    _emit(execute_show_candidate(company_id=company_id, candidate_id=candidate_id))


@app.command("review")
def review_candidate(
    company_id: int = typer.Option(help="Company ID.", min=1),
    candidate_id: int = typer.Option(help="Candidate ID.", min=1),
    yes: bool = typer.Option(False, "--yes", help="Confirm status change."),
) -> None:
    _emit(
        execute_status_change(
            company_id=company_id,
            candidate_id=candidate_id,
            yes=yes,
            transition="review",
        )
    )


@app.command("reject")
def reject_candidate(
    company_id: int = typer.Option(help="Company ID.", min=1),
    candidate_id: int = typer.Option(help="Candidate ID.", min=1),
    yes: bool = typer.Option(False, "--yes", help="Confirm status change."),
) -> None:
    _emit(
        execute_status_change(
            company_id=company_id,
            candidate_id=candidate_id,
            yes=yes,
            transition="reject",
        )
    )


def execute_list_candidates(
    *,
    company_id: int,
    status: str | None,
    limit: int,
    offset: int = 0,
    session_factory: SessionFactory | None = None,
    company_repository_factory: CompanyRepositoryFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    service_factory: ServiceFactory | None = None,
) -> CandidateCommandOutcome:
    error = _validate_list(company_id, status, limit, offset)
    if error is not None:
        return CandidateCommandOutcome(1, error_message=error)
    candidate_status = ContactDiscoveryCandidateStatus(status) if status is not None else None
    return _execute_read(
        company_id=company_id,
        failure=_LIST_FAILED,
        operation=lambda service: service.list_candidates(
            company_id, limit, offset, candidate_status
        ),
        session_factory=session_factory,
        company_repository_factory=company_repository_factory,
        repository_factory=repository_factory,
        service_factory=service_factory,
    )


def execute_show_candidate(
    *,
    company_id: int,
    candidate_id: int,
    session_factory: SessionFactory | None = None,
    company_repository_factory: CompanyRepositoryFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    service_factory: ServiceFactory | None = None,
) -> CandidateCommandOutcome:
    if not _valid_id(company_id) or not _valid_id(candidate_id):
        return CandidateCommandOutcome(1, error_message=_INVALID_ID)
    return _execute_read(
        company_id=company_id,
        failure=_SHOW_FAILED,
        operation=lambda service: service.get_candidate(company_id, candidate_id),
        session_factory=session_factory,
        company_repository_factory=company_repository_factory,
        repository_factory=repository_factory,
        service_factory=service_factory,
    )


def execute_status_change(
    *,
    company_id: int,
    candidate_id: int,
    yes: bool,
    transition: Literal["review", "reject"],
    session_factory: SessionFactory | None = None,
    company_repository_factory: CompanyRepositoryFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    service_factory: ServiceFactory | None = None,
) -> CandidateCommandOutcome:
    if not _valid_id(company_id) or not _valid_id(candidate_id):
        return CandidateCommandOutcome(1, error_message=_INVALID_ID)
    if not yes:
        return CandidateCommandOutcome(1, error_message=_CONFIRMATION_REQUIRED)
    make_session = session_factory or SessionLocal
    try:
        session = make_session()
    except Exception:
        return CandidateCommandOutcome(1, error_message=_UPDATE_FAILED)
    outcome: CandidateCommandOutcome
    close_succeeded = False
    try:
        try:
            company = (company_repository_factory or CompanyRepository)(session).get(company_id)
            if company is None:
                _safe_session_operation(session, "rollback")
                outcome = CandidateCommandOutcome(1, error_message=_COMPANY_NOT_FOUND)
            else:
                repository = (repository_factory or ContactDiscoveryRepository)(session)
                service = (service_factory or ContactDiscoveryCandidateReviewService)(repository)
                result = (
                    service.mark_reviewed(company_id, candidate_id)
                    if transition == "review"
                    else service.reject(company_id, candidate_id)
                )
                _invoke_session_operation(session, "commit")
                outcome = CandidateCommandOutcome(0, result=result)
        except ContactDiscoveryCandidateReviewNotFoundError:
            _safe_session_operation(session, "rollback")
            outcome = CandidateCommandOutcome(1, error_message=_CANDIDATE_NOT_FOUND)
        except ContactDiscoveryCandidateTransitionError:
            _safe_session_operation(session, "rollback")
            outcome = CandidateCommandOutcome(1, error_message=_TRANSITION_FORBIDDEN)
        except Exception:
            _safe_session_operation(session, "rollback")
            outcome = CandidateCommandOutcome(1, error_message=_UPDATE_FAILED)
    finally:
        close_succeeded = _safe_session_operation(session, "close")
    if not close_succeeded:
        return CandidateCommandOutcome(1, error_message=_UPDATE_FAILED)
    return outcome


def _execute_read(
    *,
    company_id: int,
    failure: str,
    operation: Callable[
        [ContactDiscoveryCandidateReviewService],
        ContactDiscoveryCandidateRead | list[ContactDiscoveryCandidateRead],
    ],
    session_factory: SessionFactory | None,
    company_repository_factory: CompanyRepositoryFactory | None,
    repository_factory: RepositoryFactory | None,
    service_factory: ServiceFactory | None,
) -> CandidateCommandOutcome:
    try:
        session = (session_factory or SessionLocal)()
    except Exception:
        return CandidateCommandOutcome(1, error_message=failure)
    try:
        company = (company_repository_factory or CompanyRepository)(session).get(company_id)
        if company is None:
            outcome = CandidateCommandOutcome(1, error_message=_COMPANY_NOT_FOUND)
        else:
            repository = (repository_factory or ContactDiscoveryRepository)(session)
            service = (service_factory or ContactDiscoveryCandidateReviewService)(repository)
            outcome = CandidateCommandOutcome(0, result=operation(service))
    except ContactDiscoveryCandidateReviewNotFoundError:
        outcome = CandidateCommandOutcome(1, error_message=_CANDIDATE_NOT_FOUND)
    except Exception:
        outcome = CandidateCommandOutcome(1, error_message=failure)
    if not _safe_session_operation(session, "close"):
        return CandidateCommandOutcome(1, error_message=failure)
    return outcome


def _validate_list(company_id: int, status: str | None, limit: int, offset: int) -> str | None:
    if not _valid_id(company_id):
        return _INVALID_COMPANY_ID
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return _INVALID_LIMIT
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return _INVALID_OFFSET
    if status is not None:
        try:
            ContactDiscoveryCandidateStatus(status)
        except (TypeError, ValueError):
            return _INVALID_STATUS
    return None


def _valid_id(value: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _invoke_session_operation(session: Session, operation: str) -> None:
    if operation not in ("commit", "rollback", "close"):
        raise ValueError("Unsupported session operation.")
    callback = getattr(session, operation, None)
    if not callable(callback):
        raise TypeError("Session operation is not callable.")
    callback()


def _safe_session_operation(session: Session, operation: str) -> bool:
    try:
        _invoke_session_operation(session, operation)
    except Exception:
        return False
    return True


def _emit(outcome: CandidateCommandOutcome) -> None:
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    elif isinstance(outcome.result, list):
        for candidate in outcome.result[:_MAX_LIST_LIMIT]:
            _print_candidate(candidate)
    elif isinstance(outcome.result, ContactDiscoveryCandidateReviewResult):
        _print_review_result(outcome.result)
    elif isinstance(outcome.result, ContactDiscoveryCandidateRead):
        _print_candidate(outcome.result)
    raise typer.Exit(outcome.exit_code)


def _print_candidate(candidate: ContactDiscoveryCandidateRead) -> None:
    values = (
        ("Candidate ID", str(candidate.id)),
        ("Company ID", str(candidate.company_id)),
        ("Status", candidate.discovery_status.value),
        ("Name", candidate.name),
        ("Title", candidate.title),
        ("Email", candidate.email),
        ("Phone", candidate.phone),
        ("Source type", candidate.source_type.value),
        ("Confidence", str(candidate.confidence)),
        ("Created", str(candidate.created_at)),
        ("Updated", str(candidate.updated_at)),
    )
    for label, value in values:
        typer.echo(f"{label}: {_display(value)}")


def _print_review_result(result: ContactDiscoveryCandidateReviewResult) -> None:
    typer.echo(f"Candidate ID: {result.candidate.id}")
    typer.echo(f"Company ID: {result.candidate.company_id}")
    typer.echo(f"Previous Status: {result.previous_status.value}")
    typer.echo(f"Current Status: {result.current_status.value}")
    typer.echo(f"Changed: {'yes' if result.changed else 'no'}")


def _display(value: str | None) -> str:
    if value is None:
        return "-"
    printable = "".join(character if character.isprintable() else " " for character in value)
    return " ".join(printable.split())[:_MAX_DISPLAY_LENGTH] or "-"

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from app.cli.contact_discovery_candidates import app as candidate_app
from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact_discovery.models import ContactDiscoveryStatus
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.contact_discovery.service import (
    ContactDiscoveryProvider,
    ContactDiscoveryRunResult,
    ContactDiscoveryService,
)
from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProvider

_COMPANY_NOT_FOUND = "company_not_found"
_COMPANY_WEBSITE_MISSING = "company_website_missing"
_PERSIST_CONFIRMATION_REQUIRED = "persist_confirmation_required"
_EXECUTION_FAILED = "contact_discovery_execution_failed"
_MAX_DISPLAY_CANDIDATES = 100
_MAX_DISPLAY_ERRORS = 20
_MAX_DISPLAY_VALUE_LENGTH = 160

SessionFactory = Callable[[], Session]
ProviderFactory = Callable[[], ContactDiscoveryProvider]
ServiceFactory = Callable[
    [ContactDiscoveryRepository, ContactDiscoveryProvider], ContactDiscoveryService
]

app = typer.Typer(help="Contact discovery commands.")
app.add_typer(candidate_app, name="candidate")


@dataclass(frozen=True)
class ContactDiscoveryCommandOutcome:
    exit_code: int
    result: ContactDiscoveryRunResult | None = None
    error_code: str | None = None


@app.callback()
def contact_discovery_commands() -> None:
    """Run contact discovery commands."""


@app.command("run")
def run_contact_discovery(
    company_id: Annotated[
        list[int],
        typer.Option(min=1, help="Existing company ID to process."),
    ],
    persist: Annotated[
        bool,
        typer.Option("--persist", help="Persist staging candidates and final state."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm the requested persist operation."),
    ] = False,
) -> None:
    """Run contact discovery for one saved company."""
    if len(company_id) != 1:
        raise typer.BadParameter("Exactly one value is required.", param_hint="--company-id")
    outcome = execute_contact_discovery(
        company_id=company_id[0],
        persist=persist,
        yes=yes,
    )
    if outcome.error_code is not None:
        typer.echo(outcome.error_code)
    elif outcome.result is not None:
        _print_result(outcome.result)
    if outcome.exit_code:
        raise typer.Exit(outcome.exit_code)


def execute_contact_discovery(
    *,
    company_id: int,
    persist: bool,
    yes: bool,
    session_factory: SessionFactory | None = None,
    provider_factory: ProviderFactory | None = None,
    service_factory: ServiceFactory | None = None,
) -> ContactDiscoveryCommandOutcome:
    """Execute one CLI run with injectable runtime dependencies."""
    if isinstance(company_id, bool) or not isinstance(company_id, int) or company_id <= 0:
        raise ValueError("Company ID must be a positive integer.")
    if persist and not yes:
        return ContactDiscoveryCommandOutcome(
            exit_code=1,
            error_code=_PERSIST_CONFIRMATION_REQUIRED,
        )

    make_session = session_factory or SessionLocal
    make_provider = provider_factory or WebsiteContactDiscoveryProvider
    make_service = service_factory or ContactDiscoveryService

    try:
        session = make_session()
    except Exception:
        return _execution_failed_outcome()

    close_succeeded = True
    try:
        outcome = _execute_with_session(
            session=session,
            company_id=company_id,
            persist=persist,
            make_provider=make_provider,
            make_service=make_service,
        )
    finally:
        close_succeeded = _safe_close(session)
    if not close_succeeded:
        return _execution_failed_outcome()
    return outcome


def _execute_with_session(
    *,
    session: Session,
    company_id: int,
    persist: bool,
    make_provider: ProviderFactory,
    make_service: ServiceFactory,
) -> ContactDiscoveryCommandOutcome:
    try:
        company = CompanyRepository(session).get(company_id)
        if company is None:
            return _outcome_after_rollback(session, _COMPANY_NOT_FOUND)
        website = company.website
        if website is None or not website.strip():
            return _outcome_after_rollback(session, _COMPANY_WEBSITE_MISSING)

        provider = make_provider()
        service = make_service(ContactDiscoveryRepository(session), provider)
        result = service.run(
            company_id=company.id,
            website_url=website,
            dry_run=not persist,
        )
        if persist:
            session.commit()
        elif not _safe_rollback(session):
            return _execution_failed_outcome()
    except Exception:
        _safe_rollback(session)
        return _execution_failed_outcome()

    return ContactDiscoveryCommandOutcome(
        exit_code=1 if result.status == ContactDiscoveryStatus.FAILED else 0,
        result=result,
    )


def _outcome_after_rollback(session: Session, local_error: str) -> ContactDiscoveryCommandOutcome:
    if not _safe_rollback(session):
        return _execution_failed_outcome()
    return ContactDiscoveryCommandOutcome(exit_code=1, error_code=local_error)


def _safe_rollback(session: Session) -> bool:
    try:
        session.rollback()
    except Exception:
        return False
    return True


def _safe_close(session: Session) -> bool:
    try:
        session.close()
    except Exception:
        return False
    return True


def _execution_failed_outcome() -> ContactDiscoveryCommandOutcome:
    return ContactDiscoveryCommandOutcome(exit_code=1, error_code=_EXECUTION_FAILED)


def _print_result(result: ContactDiscoveryRunResult) -> None:
    typer.echo("Contact discovery")
    typer.echo(f"Company ID: {result.company_id}")
    typer.echo(f"Mode: {'DRY_RUN' if result.dry_run else 'PERSIST'}")
    typer.echo(f"Status: {result.status.value}")
    typer.echo(f"Candidates: {len(result.candidates)}")
    typer.echo(f"Attempted pages: {result.attempted_pages}")
    typer.echo(f"Successful pages: {result.successful_pages}")
    typer.echo(f"Selected secondary URLs: {result.selected_urls}")
    typer.echo(f"Candidate upserts: {result.candidate_upserts}")
    typer.echo(f"State persisted: {_yes_no(result.state_persisted)}")
    typer.echo(f"Limited link scan: {_yes_no(result.limited_link_scan)}")
    displayed_errors = tuple(_display_value(error) for error in result.errors[:_MAX_DISPLAY_ERRORS])
    typer.echo(f"Errors: {', '.join(displayed_errors) or 'none'}")
    omitted_errors = len(result.errors) - len(displayed_errors)
    if omitted_errors:
        typer.echo(f"Omitted errors: {omitted_errors}")

    displayed = result.candidates[:_MAX_DISPLAY_CANDIDATES]
    for index, candidate in enumerate(displayed, start=1):
        typer.echo(f"Candidate {index}")
        typer.echo(f"  Name: {_display_value(candidate.name)}")
        typer.echo(f"  Title: {_display_value(candidate.title)}")
        typer.echo(f"  Email: {_display_value(candidate.email)}")
        typer.echo(f"  Phone: {_display_value(candidate.phone)}")
        typer.echo(f"  Source type: {candidate.source_type.value}")
        typer.echo(f"  Confidence: {candidate.confidence}")
    omitted = len(result.candidates) - len(displayed)
    if omitted:
        typer.echo(f"Omitted candidates: {omitted}")


def _display_value(value: str | None) -> str:
    if value is None:
        return "-"
    printable = "".join(character if character.isprintable() else " " for character in value)
    normalized = " ".join(printable.split())
    return normalized[:_MAX_DISPLAY_VALUE_LENGTH] or "-"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"

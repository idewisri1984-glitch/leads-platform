from collections.abc import Callable
from dataclasses import dataclass

import typer
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.contact import ContactRepository
from app.modules.lead import (
    ContactLeadCreationConsistencyError,
    ContactLeadCreationInvalidDataError,
    ContactLeadCreationNotFoundError,
    ContactLeadCreationResult,
    ContactLeadCreationService,
    LeadCreate,
    LeadRepository,
    LeadService,
)

app = typer.Typer(help="Lead management commands.")

SessionFactory = Callable[[], Session]
ContactRepositoryFactory = Callable[[Session], ContactRepository]
LeadRepositoryFactory = Callable[[Session], LeadRepository]
ContactLeadCreationServiceFactory = Callable[
    [ContactRepository, LeadRepository],
    ContactLeadCreationService,
]

_CONFIRMATION_REQUIRED = "Lead creation requires --yes."
_INVALID_DATA = "Lead creation data is invalid."
_NOT_FOUND = "Contact was not found."
_INCONSISTENT_STATE = "Lead creation state is inconsistent."
_CREATION_FAILED = "Lead creation failed."


@dataclass(frozen=True)
class ContactLeadCreationCommandOutcome:
    exit_code: int
    result: ContactLeadCreationResult | None = None
    error_message: str | None = None


def execute_create_lead_from_contact(
    *,
    company_id: int,
    contact_id: int,
    yes: bool,
    session_factory: SessionFactory | None = None,
    contact_repository_factory: ContactRepositoryFactory | None = None,
    lead_repository_factory: LeadRepositoryFactory | None = None,
    service_factory: ContactLeadCreationServiceFactory | None = None,
) -> ContactLeadCreationCommandOutcome:
    if yes is not True:
        return ContactLeadCreationCommandOutcome(
            exit_code=1,
            error_message=_CONFIRMATION_REQUIRED,
        )
    if not _valid_contact_lead_id(company_id) or not _valid_contact_lead_id(contact_id):
        return ContactLeadCreationCommandOutcome(
            exit_code=1,
            error_message=_INVALID_DATA,
        )

    try:
        session = (session_factory or SessionLocal)()
    except Exception:
        return ContactLeadCreationCommandOutcome(
            exit_code=1,
            error_message=_CREATION_FAILED,
        )

    committed = False
    rollback_attempted = False
    outcome: ContactLeadCreationCommandOutcome
    try:
        try:
            contact_repository = (contact_repository_factory or ContactRepository)(session)
            lead_repository = (lead_repository_factory or LeadRepository)(session)
            service = (service_factory or ContactLeadCreationService)(
                contact_repository,
                lead_repository,
            )
            result = service.create(company_id, contact_id)
            session.commit()
            committed = True
            outcome = ContactLeadCreationCommandOutcome(exit_code=0, result=result)
        except (
            ContactLeadCreationInvalidDataError,
            ContactLeadCreationNotFoundError,
            ContactLeadCreationConsistencyError,
        ) as error:
            rollback_attempted = True
            if _rollback_contact_lead_session(session):
                outcome = ContactLeadCreationCommandOutcome(
                    exit_code=1,
                    error_message=_contact_lead_creation_error_message(error),
                )
            else:
                outcome = ContactLeadCreationCommandOutcome(
                    exit_code=1,
                    error_message=_CREATION_FAILED,
                )
        except Exception:
            if not committed and not rollback_attempted:
                rollback_attempted = True
                _rollback_contact_lead_session(session)
            outcome = ContactLeadCreationCommandOutcome(
                exit_code=1,
                error_message=_CREATION_FAILED,
            )
    finally:
        try:
            session.close()
        except Exception:
            close_succeeded = False
        else:
            close_succeeded = True

    if not close_succeeded:
        return ContactLeadCreationCommandOutcome(
            exit_code=1,
            error_message=_CREATION_FAILED,
        )
    return outcome


def _valid_contact_lead_id(value: object) -> bool:
    return type(value) is int and value > 0


def _rollback_contact_lead_session(session: Session) -> bool:
    try:
        session.rollback()
    except Exception:
        return False
    return True


def _contact_lead_creation_error_message(
    error: ContactLeadCreationInvalidDataError
    | ContactLeadCreationNotFoundError
    | ContactLeadCreationConsistencyError,
) -> str:
    if isinstance(error, ContactLeadCreationInvalidDataError):
        return _INVALID_DATA
    if isinstance(error, ContactLeadCreationNotFoundError):
        return _NOT_FOUND
    return _INCONSISTENT_STATE


def _print_contact_lead_creation_result(result: ContactLeadCreationResult) -> None:
    typer.echo(f"Lead ID: {result.lead_id}")
    typer.echo(f"Company ID: {result.company_id}")
    typer.echo(f"Contact ID: {result.contact_id}")
    typer.echo(f"Status: {result.status}")


@app.command("create-from-contact")
def create_lead_from_contact(
    company_id: int = typer.Option(help="Company ID."),
    contact_id: int = typer.Option(help="Contact ID."),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm Lead creation.",
    ),
) -> None:
    outcome = execute_create_lead_from_contact(
        company_id=company_id,
        contact_id=contact_id,
        yes=yes,
    )
    if outcome.error_message is not None:
        typer.secho(outcome.error_message, fg=typer.colors.RED)
    if outcome.result is not None:
        _print_contact_lead_creation_result(outcome.result)
    raise typer.Exit(outcome.exit_code)


@app.command("create")
def create_lead(
    company_id: int,
    contact_id: int | None = None,
    status: str = "NEW",
    source: str = "",
    notes: str = "",
) -> None:
    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))

        try:
            lead = service.create(
                LeadCreate(
                    company_id=company_id,
                    contact_id=contact_id,
                    status=status,
                    source=source or None,
                    notes=notes or None,
                )
            )
        except ValueError as error:
            typer.secho(str(error), fg=typer.colors.RED)
            raise typer.Exit(1) from error

    typer.secho("Lead created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {lead.id}")
    typer.echo(f"Company ID: {lead.company_id}")
    typer.echo(f"Contact ID: {lead.contact_id}")
    typer.echo(f"Status: {lead.status}")
    typer.echo(f"Source: {lead.source}")


@app.command("list")
def list_leads() -> None:
    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))

        leads = service.get_all()

    if not leads:
        typer.echo("No leads found.")
        return

    typer.echo("\nLeads\n")

    for lead in leads:
        contact_id = str(lead.contact_id) if lead.contact_id is not None else "-"
        source = lead.source or "-"
        typer.echo(f"{lead.id:>3}  {lead.company_id:>3}  {contact_id:>3}  {lead.status}  {source}")


@app.command("show")
def show_lead(lead_id: int) -> None:
    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))

        lead = service.get(lead_id)

    if lead is None:
        typer.secho("Lead not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo()
    typer.echo(f"ID:         {lead.id}")
    typer.echo(f"Company ID: {lead.company_id}")
    typer.echo(f"Contact ID: {lead.contact_id}")
    typer.echo(f"Status:     {lead.status}")
    typer.echo(f"Source:     {lead.source}")
    typer.echo(f"Notes:      {lead.notes}")


@app.command("delete")
def delete_lead(lead_id: int) -> None:
    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))

        lead = repository.get(lead_id)

        if lead is None:
            typer.secho("Lead not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        service.delete(lead)

    typer.secho("Lead deleted", fg=typer.colors.GREEN)

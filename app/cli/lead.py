import typer

from app.core.database.session import SessionLocal
from app.modules.contact import ContactRepository
from app.modules.lead import LeadCreate, LeadRepository, LeadService

app = typer.Typer(help="Lead management commands.")


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

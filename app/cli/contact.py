import typer

from app.core.database.session import SessionLocal
from app.modules.contact import (
    ContactCreate,
    ContactRepository,
    ContactService,
)

app = typer.Typer(help="Contact management commands.")


@app.command("create")
def create_contact(
    company_id: int,
    first_name: str,
    last_name: str = "",
    job_title: str = "",
    email: str = "",
    phone: str = "",
    linkedin_url: str = "",
    country: str = "",
    city: str = "",
    source: str = "",
) -> None:
    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        contact = service.create(
            ContactCreate(
                company_id=company_id,
                first_name=first_name,
                last_name=last_name or None,
                job_title=job_title or None,
                email=email or None,
                phone=phone or None,
                linkedin_url=linkedin_url or None,
                country=country or None,
                city=city or None,
                source=source or None,
            )
        )

    typer.secho("Contact created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {contact.id}")
    typer.echo(f"Company ID: {contact.company_id}")
    typer.echo(f"Name: {contact.first_name} {contact.last_name or ''}".rstrip())


@app.command("list")
def list_contacts(company_id: int | None = None) -> None:
    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        contacts = service.get_all() if company_id is None else service.get_by_company(company_id)

    if not contacts:
        typer.echo("No contacts found.")
        return

    typer.echo("\nContacts\n")

    for contact in contacts:
        full_name = f"{contact.first_name} {contact.last_name or ''}".rstrip()
        typer.echo(f"{contact.id:>3}  {contact.company_id:>3}  {full_name}")


@app.command("show")
def show_contact(contact_id: int) -> None:
    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        contact = service.get(contact_id)

    if contact is None:
        typer.secho("Contact not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo()
    typer.echo(f"ID:           {contact.id}")
    typer.echo(f"Company ID:   {contact.company_id}")
    typer.echo(f"First name:   {contact.first_name}")
    typer.echo(f"Last name:    {contact.last_name}")
    typer.echo(f"Job title:    {contact.job_title}")
    typer.echo(f"Email:        {contact.email}")
    typer.echo(f"Phone:        {contact.phone}")
    typer.echo(f"LinkedIn URL: {contact.linkedin_url}")
    typer.echo(f"Country:      {contact.country}")
    typer.echo(f"City:         {contact.city}")
    typer.echo(f"Source:       {contact.source}")
    typer.echo(f"External ID:  {contact.external_id}")
    typer.echo(f"Status:       {contact.status}")
    typer.echo(f"Notes:        {contact.notes}")


@app.command("delete")
def delete_contact(contact_id: int) -> None:
    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        contact = repository.get(contact_id)

        if contact is None:
            typer.secho("Contact not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        service.delete(contact)

    typer.secho("Contact deleted", fg=typer.colors.GREEN)

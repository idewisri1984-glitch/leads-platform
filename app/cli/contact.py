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
    first_name: str = "",
    last_name: str = "",
    job_title: str = "",
    email: str = "",
    phone: str = "",
    linkedin_url: str = "",
    instagram_url: str = "",
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
                first_name=first_name or None,
                last_name=last_name or None,
                job_title=job_title or None,
                email=email or None,
                phone=phone or None,
                linkedin_url=linkedin_url or None,
                instagram_url=instagram_url or None,
                country=country or None,
                city=city or None,
                source=source or None,
            )
        )

    typer.secho("Contact created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {contact.id}")
    typer.echo(f"Company ID: {contact.company_id}")
    typer.echo(f"Name: {_contact_display_name(contact)}")


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
        typer.echo(f"{contact.id:>3}  {contact.company_id:>3}  {_contact_display_name(contact)}")


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
    typer.echo(f"First name:    {_display_value(contact.first_name)}")
    typer.echo(f"Last name:     {_display_value(contact.last_name)}")
    typer.echo(f"Job title:     {_display_value(contact.job_title)}")
    typer.echo(f"Email:         {_display_value(contact.email)}")
    typer.echo(f"Phone:         {_display_value(contact.phone)}")
    typer.echo(f"LinkedIn URL:  {_display_value(contact.linkedin_url)}")
    typer.echo(f"Instagram URL: {_display_value(contact.instagram_url)}")
    typer.echo(f"Country:       {_display_value(contact.country)}")
    typer.echo(f"City:          {_display_value(contact.city)}")
    typer.echo(f"Source:        {_display_value(contact.source)}")
    typer.echo(f"External ID:   {_display_value(contact.external_id)}")
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


def _contact_display_name(contact: object) -> str:
    first_name = getattr(contact, "first_name", None)
    last_name = getattr(contact, "last_name", None)
    name = " ".join(value for value in (first_name, last_name) if value)
    if name:
        return name
    for field in ("email", "phone", "linkedin_url", "instagram_url"):
        value = getattr(contact, field, None)
        if value:
            return str(value)
    return "-"


def _display_value(value: str | None) -> str:
    return value or "-"

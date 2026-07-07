import typer

from app.core.database.session import SessionLocal
from app.modules.company import (
    CompanyCreate,
    CompanyRepository,
    CompanyService,
)

app = typer.Typer(help="Company management commands.")


@app.command("create")
def create_company(
    project_id: int,
    name: str,
    website: str = "",
    country: str = "",
    city: str = "",
    industry: str = "",
) -> None:
    with SessionLocal() as session:
        repository = CompanyRepository(session)
        service = CompanyService(repository)

        company = service.create(
            CompanyCreate(
                project_id=project_id,
                name=name,
                website=website or None,
                country=country or None,
                city=city or None,
                industry=industry or None,
            )
        )

    typer.secho("OK: Company created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {company.id}")
    typer.echo(f"Project ID: {company.project_id}")
    typer.echo(f"Name: {company.name}")


@app.command("list")
def list_companies() -> None:
    with SessionLocal() as session:
        repository = CompanyRepository(session)
        service = CompanyService(repository)

        companies = service.get_all()

    if not companies:
        typer.echo("No companies found.")
        return

    typer.echo("\nCompanies\n")

    for company in companies:
        typer.echo(f"{company.id:>3}  {company.project_id:>3}  {company.name}")


@app.command("show")
def show_company(company_id: int) -> None:
    with SessionLocal() as session:
        repository = CompanyRepository(session)
        service = CompanyService(repository)

        company = service.get(company_id)

    if company is None:
        typer.secho("Company not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo()
    typer.echo(f"ID:         {company.id}")
    typer.echo(f"Project ID: {company.project_id}")
    typer.echo(f"Name:       {company.name}")
    typer.echo(f"Website:    {company.website}")
    typer.echo(f"Country:    {company.country}")
    typer.echo(f"City:       {company.city}")
    typer.echo(f"Industry:   {company.industry}")
    typer.echo(f"Status:     {company.status}")
    typer.echo(f"Notes:      {company.notes}")


@app.command("delete")
def delete_company(company_id: int) -> None:
    with SessionLocal() as session:
        repository = CompanyRepository(session)
        service = CompanyService(repository)

        company = repository.get(company_id)

        if company is None:
            typer.secho("Company not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        service.delete(company)

    typer.secho("OK: Company deleted", fg=typer.colors.GREEN)

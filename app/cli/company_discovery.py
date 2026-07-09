from typing import Annotated

import typer
from pydantic import ValidationError

from app.core.config.settings import settings
from app.core.database.session import SessionLocal
from app.modules.company_discovery import CompanyDiscoveryRequest, CompanyDiscoveryService
from app.modules.company_import.schemas import CompanyIngestionError
from app.providers.serpapi import SerpApiClient, SerpApiError

app = typer.Typer(help="Company discovery commands.")


@app.command("serpapi")
def discover_serpapi(
    query: Annotated[str | None, typer.Option(help="Search query text.")] = None,
    country: Annotated[str | None, typer.Option(help="Country filter text.")] = None,
    city: Annotated[str | None, typer.Option(help="City filter text.")] = None,
    industry: Annotated[str | None, typer.Option(help="Industry filter text.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum provider results to parse.")] = 10,
    persist: Annotated[
        bool,
        typer.Option(
            "--persist/--dry-run",
            help="Persist discovered companies through the ingestion service.",
        ),
    ] = False,
    project_id: Annotated[
        int | None,
        typer.Option(help="Project ID required when --persist is used."),
    ] = None,
) -> None:
    try:
        request = CompanyDiscoveryRequest(
            query=query,
            country=country,
            city=city,
            industry=industry,
            limit=limit,
        )
    except ValidationError as error:
        typer.secho(
            f"Invalid discovery request: {_first_validation_message(error)}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from error

    if persist and project_id is None:
        typer.secho("--project-id is required when --persist is used.", fg=typer.colors.RED)
        raise typer.Exit(1)

    client = SerpApiClient(
        api_key=settings.serpapi_api_key,
        base_url=settings.serpapi_base_url,
        timeout_seconds=settings.serpapi_timeout_seconds,
    )
    service = CompanyDiscoveryService(client)

    try:
        if persist:
            if project_id is None:
                raise AssertionError("project_id must be validated before persistence.")

            with SessionLocal() as session:
                persistence_result = service.discover_and_ingest_from_serpapi(
                    session=session,
                    project_id=project_id,
                    request=request,
                )
        else:
            result = service.discover_from_serpapi(request)
    except SerpApiError as error:
        typer.secho(f"SerpAPI error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1) from error

    if persist:
        typer.echo(f"Provider: {persistence_result.provider}")
        typer.echo(f"Query: {persistence_result.query}")
        typer.echo(f"Discovered: {persistence_result.discovered}")
        typer.echo(f"Imported: {persistence_result.imported}")
        typer.echo(f"Skipped duplicates: {persistence_result.skipped_duplicates}")
        typer.echo(f"Failed: {persistence_result.failed}")
        typer.echo(f"Created company IDs: {_format_ids(persistence_result.created_company_ids)}")
        typer.echo(f"Rolled back: {persistence_result.rolled_back}")
        _print_errors("Errors", persistence_result.errors)
        return

    typer.echo(f"Provider: {result.provider}")
    typer.echo(f"Query: {result.query}")
    typer.echo(f"Total results: {result.total_results}")

    if result.items:
        typer.echo()
        typer.echo("Discovered companies")

        for item in result.items:
            typer.echo(f"- Name: {item.name}")
            typer.echo(f"  Website: {item.website or ''}")
            typer.echo(f"  Country: {item.country or ''}")
            typer.echo(f"  City: {item.city or ''}")
            typer.echo(f"  Industry: {item.industry or ''}")

    _print_errors("Adapter errors", result.errors)


def _first_validation_message(error: ValidationError) -> str:
    validation_errors = error.errors()

    if not validation_errors:
        return "Invalid discovery request."

    first_error = validation_errors[0]
    message = first_error.get("msg", "Invalid discovery request.")

    if isinstance(message, str):
        return message

    return "Invalid discovery request."


def _format_ids(company_ids: list[int]) -> str:
    if not company_ids:
        return ""

    return ", ".join(str(company_id) for company_id in company_ids)


def _print_errors(title: str, errors: list[CompanyIngestionError]) -> None:
    if not errors:
        return

    typer.echo()
    typer.echo(title)

    for error in errors:
        row = error.source_row_number if error.source_row_number is not None else ""
        typer.echo(f"- Source row: {row}  Code: {error.code}  Message: {error.message}")

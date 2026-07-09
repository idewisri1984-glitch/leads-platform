from typing import Annotated

import typer
from pydantic import ValidationError

from app.core.config.settings import settings
from app.modules.company_discovery import CompanyDiscoveryRequest, CompanyDiscoveryService
from app.providers.serpapi import SerpApiClient, SerpApiError

app = typer.Typer(help="Company discovery commands.")


@app.command("serpapi")
def discover_serpapi(
    query: Annotated[str | None, typer.Option(help="Search query text.")] = None,
    country: Annotated[str | None, typer.Option(help="Country filter text.")] = None,
    city: Annotated[str | None, typer.Option(help="City filter text.")] = None,
    industry: Annotated[str | None, typer.Option(help="Industry filter text.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum provider results to parse.")] = 10,
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

    client = SerpApiClient(
        api_key=settings.serpapi_api_key,
        base_url=settings.serpapi_base_url,
        timeout_seconds=settings.serpapi_timeout_seconds,
    )
    service = CompanyDiscoveryService(client)

    try:
        result = service.discover_from_serpapi(request)
    except SerpApiError as error:
        typer.secho(f"SerpAPI error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1) from error

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

    if result.errors:
        typer.echo()
        typer.echo("Adapter errors")

        for adapter_error in result.errors:
            row = (
                adapter_error.source_row_number
                if adapter_error.source_row_number is not None
                else ""
            )
            typer.echo(
                f"- Source row: {row}  Code: {adapter_error.code}  Message: {adapter_error.message}"
            )


def _first_validation_message(error: ValidationError) -> str:
    validation_errors = error.errors()

    if not validation_errors:
        return "Invalid discovery request."

    first_error = validation_errors[0]
    message = first_error.get("msg", "Invalid discovery request.")

    if isinstance(message, str):
        return message

    return "Invalid discovery request."

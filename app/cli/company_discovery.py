from typing import Annotated

import typer
from pydantic import ValidationError

from app.core.config.settings import settings
from app.core.database.session import SessionLocal
from app.modules.company_discovery import (
    CompanyDiscoveryRequest,
    CompanyDiscoveryService,
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryExecutionError,
    SearchProfileDiscoveryService,
    SerpApiDiscoveryProvider,
)
from app.modules.company_import.schemas import CompanyIngestionError
from app.modules.search_profile import (
    SearchProfileQueryGenerationError,
    SearchProfileQueryGenerator,
    SearchProfileRepository,
    SearchProfileRunOptions,
    SearchProfileService,
)
from app.providers.serpapi import SerpApiClient, SerpApiError

app = typer.Typer(help="Company discovery commands.")


@app.command("run-profile")
def run_search_profile(
    profile_id: Annotated[int, typer.Option(help="Search profile ID to execute.")],
    provider: Annotated[str, typer.Option(help="Discovery provider name.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Execute without persisting companies."),
    ] = False,
    max_queries: Annotated[
        int | None,
        typer.Option(help="Lower the maximum query count."),
    ] = None,
    result_limit_per_query: Annotated[
        int | None,
        typer.Option(help="Lower the result limit per query."),
    ] = None,
    total_result_ceiling: Annotated[
        int | None,
        typer.Option(help="Lower the total result ceiling."),
    ] = None,
) -> None:
    """Execute an existing search profile through SerpAPI without persistence."""
    if not dry_run:
        typer.secho("--dry-run is required for search profile execution.", fg=typer.colors.RED)
        raise typer.Exit(1)

    if provider.strip().casefold() != "serpapi":
        typer.secho(
            f"Unsupported discovery provider: {provider}. Stage D2 supports only serpapi.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    try:
        options = SearchProfileRunOptions(
            max_queries=max_queries,
            result_limit_per_query=result_limit_per_query,
            total_result_ceiling=total_result_ceiling,
        )
    except ValidationError as error:
        typer.secho(
            f"Invalid search profile run options: {_first_validation_message(error)}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from error

    with SessionLocal() as session:
        profile = SearchProfileService(SearchProfileRepository(session)).get(profile_id)

    if profile is None:
        typer.secho(f"Search profile {profile_id} not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    client = SerpApiClient(
        api_key=settings.serpapi_api_key,
        base_url=settings.serpapi_base_url,
        timeout_seconds=settings.serpapi_timeout_seconds,
    )
    discovery_provider = SerpApiDiscoveryProvider(client)
    service = SearchProfileDiscoveryService(SearchProfileQueryGenerator())

    try:
        report = service.run_dry(profile, discovery_provider, options)
    except SearchProfileDiscoveryExecutionError as error:
        typer.secho(f"Search profile execution error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1) from error
    except SearchProfileQueryGenerationError as error:
        typer.secho(f"Search profile query error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1) from error

    _print_profile_dry_run_report(report)


@app.callback(invoke_without_command=True)
@app.command("serpapi")
def discover_serpapi(
    ctx: typer.Context,
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
    if ctx.invoked_subcommand is not None:
        return

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

        if persistence_result.rolled_back:
            typer.secho(
                "Persistence failed; transaction was rolled back.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)

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


def _print_profile_dry_run_report(report: SearchProfileDiscoveryDryRunResult) -> None:
    typer.echo("Dry run: True")
    typer.echo(f"Profile ID: {report.profile_id}")
    typer.echo(f"Profile Name: {report.profile_name}")
    typer.echo(f"Provider: {report.provider}")
    typer.echo(f"Query Count: {report.query_count}")
    typer.echo(f"Estimated Provider Requests: {report.estimated_provider_requests}")
    typer.echo(f"Executed Queries: {report.executed_queries}")
    typer.echo(f"Total Provider Results: {report.total_provider_results}")
    typer.echo(f"Total Adapted Items: {report.total_adapted_items}")
    typer.echo(f"Total Adapter Errors: {report.total_adapter_errors}")
    typer.echo(f"Total Provider Errors: {report.total_provider_errors}")
    typer.echo(f"Total Result Ceiling: {report.total_result_ceiling}")
    typer.echo(f"Stopped Early: {report.stopped_early}")
    typer.echo(f"Stop Reason: {report.stop_reason or ''}")
    typer.echo("Companies persisted: 0")

    for number, query_result in enumerate(report.query_results, start=1):
        typer.echo(f"\nQuery {number}")
        typer.echo(f"Text: {query_result.query.text}")
        typer.echo(f"Country: {query_result.query.country or ''}")
        typer.echo(f"City: {query_result.query.city or ''}")
        typer.echo(f"Language: {query_result.query.language or ''}")
        typer.echo(f"Limit: {query_result.query.limit}")
        typer.echo(f"Provider Results: {query_result.provider_result_count}")
        typer.echo(f"Adapted Items: {query_result.adapted_item_count}")
        typer.echo(f"Adapter Errors: {query_result.adapter_error_count}")

        if query_result.provider_error is not None:
            typer.echo(f"Provider Error Code: {query_result.provider_error.code}")
            typer.echo(f"Provider Error: {query_result.provider_error.message}")

        for item in query_result.items:
            typer.echo(f"- Name: {item.name}")
            typer.echo(f"  Website: {item.website or ''}")
            typer.echo(f"  Country: {item.country or ''}")
            typer.echo(f"  City: {item.city or ''}")
            typer.echo(f"  Industry: {item.industry or ''}")

        for error in query_result.adapter_errors:
            position = error.position if error.position is not None else ""
            typer.echo(f"- Adapter error at position {position}: {error.message}")

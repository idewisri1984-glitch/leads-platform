from typing import Annotated, Never

import typer
from pydantic import ValidationError

from app.core.database.session import SessionLocal
from app.modules.search_profile import (
    SearchProfileCreate,
    SearchProfileQueryGenerationError,
    SearchProfileQueryGenerator,
    SearchProfileRepository,
    SearchProfileRunOptions,
    SearchProfileService,
)

app = typer.Typer(help="Search profile management and query preview commands.")


@app.command("create")
def create_search_profile(
    project_id: Annotated[int, typer.Option(help="Owning project ID.")],
    name: Annotated[str, typer.Option(help="Profile name.")],
    product_or_service: Annotated[str, typer.Option(help="Product or service to target.")],
    target_customer_type: Annotated[
        list[str] | None, typer.Option(help="Target customer type; repeat as needed.")
    ] = None,
    target_industry: Annotated[
        list[str] | None, typer.Option(help="Target industry; repeat as needed.")
    ] = None,
    positive_keyword: Annotated[
        list[str] | None, typer.Option(help="Positive keyword; repeat as needed.")
    ] = None,
    negative_keyword: Annotated[
        list[str] | None, typer.Option(help="Negative keyword; repeat as needed.")
    ] = None,
    country: Annotated[
        list[str] | None, typer.Option(help="Country target; repeat as needed.")
    ] = None,
    city: Annotated[list[str] | None, typer.Option(help="City target; repeat as needed.")] = None,
    language: Annotated[
        list[str] | None, typer.Option(help="Language target; repeat as needed.")
    ] = None,
    query_template: Annotated[
        list[str] | None, typer.Option(help="Query template; repeat as needed.")
    ] = None,
    description: Annotated[str | None, typer.Option(help="Profile description.")] = None,
    result_limit: Annotated[int, typer.Option(help="Maximum results per query.")] = 10,
    max_queries_per_run: Annotated[int, typer.Option(help="Maximum queries per preview.")] = 10,
    total_result_ceiling: Annotated[int, typer.Option(help="Maximum total results.")] = 100,
    enabled: Annotated[
        bool, typer.Option("--enabled/--disabled", help="Enable or disable the profile.")
    ] = True,
) -> None:
    """Create a universal search profile."""
    try:
        data = SearchProfileCreate(
            project_id=project_id,
            name=name,
            description=description,
            product_or_service=product_or_service,
            target_customer_types=target_customer_type or [],
            target_industries=target_industry or [],
            positive_keywords=positive_keyword or [],
            negative_keywords=negative_keyword or [],
            countries=country or [],
            cities=city or [],
            languages=language or [],
            query_templates=query_template or [],
            result_limit=result_limit,
            max_queries_per_run=max_queries_per_run,
            total_result_ceiling=total_result_ceiling,
            enabled=enabled,
        )
    except ValidationError as error:
        _validation_error("Invalid search profile", error)

    with SessionLocal() as session:
        profile = SearchProfileService(SearchProfileRepository(session)).create(data)

    typer.secho("OK: Search profile created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {profile.id}")


@app.command("list")
def list_search_profiles(
    project_id: Annotated[int | None, typer.Option(help="Filter by project ID.")] = None,
) -> None:
    """List search profiles, optionally filtered by project."""
    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        profiles = service.get_all() if project_id is None else service.get_by_project(project_id)

    if not profiles:
        typer.echo("No search profiles found.")
        return

    for profile in profiles:
        typer.echo(
            f"ID: {profile.id}  Project ID: {profile.project_id}  Name: {profile.name}  "
            f"Product or Service: {profile.product_or_service}  Enabled: {profile.enabled}"
        )


@app.command("show")
def show_search_profile(profile_id: int) -> None:
    """Show a search profile."""
    with SessionLocal() as session:
        profile = SearchProfileService(SearchProfileRepository(session)).get(profile_id)

    if profile is None:
        _not_found(profile_id)

    fields = (
        ("ID", profile.id),
        ("Project ID", profile.project_id),
        ("Name", profile.name),
        ("Description", profile.description or ""),
        ("Product or Service", profile.product_or_service),
        ("Target Customer Types", _format_list(profile.target_customer_types)),
        ("Target Industries", _format_list(profile.target_industries)),
        ("Positive Keywords", _format_list(profile.positive_keywords)),
        ("Negative Keywords", _format_list(profile.negative_keywords)),
        ("Countries", _format_list(profile.countries)),
        ("Cities", _format_list(profile.cities)),
        ("Languages", _format_list(profile.languages)),
        ("Query Templates", _format_list(profile.query_templates)),
        ("Result Limit", profile.result_limit),
        ("Max Queries Per Run", profile.max_queries_per_run),
        ("Total Result Ceiling", profile.total_result_ceiling),
        ("Enabled", profile.enabled),
    )
    for label, value in fields:
        typer.echo(f"{label}: {value}")


@app.command("delete")
def delete_search_profile(profile_id: int) -> None:
    """Delete a search profile."""
    with SessionLocal() as session:
        deleted = SearchProfileService(SearchProfileRepository(session)).delete(profile_id)

    if not deleted:
        _not_found(profile_id)

    typer.secho(f"OK: Search profile {profile_id} deleted", fg=typer.colors.GREEN)


@app.command("preview-queries")
def preview_queries(
    profile_id: int,
    max_queries: Annotated[int | None, typer.Option(help="Lower the maximum query count.")] = None,
    result_limit_per_query: Annotated[
        int | None, typer.Option(help="Lower the result limit per query.")
    ] = None,
    total_result_ceiling: Annotated[
        int | None, typer.Option(help="Lower the total result ceiling.")
    ] = None,
) -> None:
    """Preview deterministic queries without executing them."""
    try:
        options = SearchProfileRunOptions(
            max_queries=max_queries,
            result_limit_per_query=result_limit_per_query,
            total_result_ceiling=total_result_ceiling,
        )
    except ValidationError as error:
        _validation_error("Invalid preview options", error)

    with SessionLocal() as session:
        profile = SearchProfileService(SearchProfileRepository(session)).get(profile_id)

    if profile is None:
        _not_found(profile_id)

    try:
        preview = SearchProfileQueryGenerator().generate_preview(profile, options)
    except SearchProfileQueryGenerationError as error:
        typer.secho(f"Query preview error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1) from error

    typer.echo(f"Profile ID: {preview.profile_id}")
    typer.echo(f"Profile Name: {preview.profile_name}")
    typer.echo(f"Query Count: {preview.query_count}")
    typer.echo(f"Estimated Provider Requests: {preview.estimated_provider_requests}")
    typer.echo(f"Result Limit Per Query: {preview.result_limit_per_query}")
    typer.echo(f"Total Result Ceiling: {preview.total_result_ceiling}")

    for number, query in enumerate(preview.queries, start=1):
        typer.echo(f"\nQuery {number}")
        typer.echo(f"Text: {query.text}")
        typer.echo(f"Country: {query.country or ''}")
        typer.echo(f"City: {query.city or ''}")
        typer.echo(f"Language: {query.language or ''}")
        typer.echo(f"Limit: {query.limit}")


def _format_list(values: list[str]) -> str:
    return ", ".join(values)


def _not_found(profile_id: int) -> Never:
    typer.secho(f"Search profile {profile_id} not found.", fg=typer.colors.RED)
    raise typer.Exit(1)


def _validation_error(prefix: str, error: ValidationError) -> Never:
    details = error.errors()
    message = details[0].get("msg", "Validation failed.") if details else "Validation failed."
    safe_message = message if isinstance(message, str) else "Validation failed."
    typer.secho(f"{prefix}: {safe_message}", fg=typer.colors.RED)
    raise typer.Exit(1) from error

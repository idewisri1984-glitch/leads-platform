from typing import Annotated

import typer

from app.core.database.session import SessionLocal
from app.modules.company_enrichment import (
    CompanyEnrichmentRepository,
    CompanyEnrichmentRunResult,
    CompanyEnrichmentService,
    EnrichmentProvider,
    FakeEnrichmentProvider,
    WebsiteEnrichmentProvider,
)

app = typer.Typer(help="Company enrichment commands.")


@app.callback()
def company_enrichment_commands() -> None:
    """Run company enrichment commands."""


@app.command("run")
def run_company_enrichment(
    project_id: Annotated[int, typer.Option(help="Project ID to enrich.")],
    provider: Annotated[str, typer.Option(help="Enrichment provider name.")],
    limit: Annotated[int, typer.Option(help="Maximum companies to enrich.")] = 20,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run without persisting enrichment data."),
    ] = False,
    persist: Annotated[
        bool,
        typer.Option("--persist", help="Persist enrichment data through the service."),
    ] = False,
) -> None:
    """Run provider-based enrichment for saved companies."""
    if dry_run == persist:
        typer.secho(
            "Choose exactly one mode: --dry-run or --persist.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    if project_id <= 0:
        typer.secho("Project ID must be greater than zero.", fg=typer.colors.RED)
        raise typer.Exit(1)

    if not 1 <= limit <= 100:
        typer.secho("Limit must be between 1 and 100.", fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        enrichment_provider = _get_enrichment_provider(provider)
    except ValueError:
        typer.secho(
            "Unsupported enrichment provider. Choose one of: fake, website.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from None

    try:
        with SessionLocal() as session:
            service = CompanyEnrichmentService(CompanyEnrichmentRepository(session))
            report = service.enrich_project_companies(
                project_id=project_id,
                provider=enrichment_provider,
                limit=limit,
                dry_run=dry_run,
            )
    except Exception:
        typer.secho("Company enrichment failed safely.", fg=typer.colors.RED)
        raise typer.Exit(1) from None

    _print_report(report, project_id=project_id, limit=limit, persist=persist)


def _get_enrichment_provider(provider_name: str) -> EnrichmentProvider:
    normalized_name = provider_name.strip().casefold()
    if normalized_name == "fake":
        return FakeEnrichmentProvider()
    if normalized_name == "website":
        return WebsiteEnrichmentProvider()
    raise ValueError("Unsupported enrichment provider.")


def _print_report(
    report: CompanyEnrichmentRunResult,
    *,
    project_id: int,
    limit: int,
    persist: bool,
) -> None:
    typer.echo(f"Dry run: {report.dry_run}")
    typer.echo(f"Persistence requested: {persist}")
    typer.echo(f"Provider: {report.provider}")
    typer.echo(f"Project ID: {project_id}")
    typer.echo(f"Limit: {limit}")
    typer.echo(f"Selected: {report.selected}")
    typer.echo(f"Attempted: {report.attempted}")
    typer.echo(f"Created: {report.created}")
    typer.echo(f"Updated: {report.updated}")
    typer.echo(f"Unchanged: {report.unchanged}")
    typer.echo(f"Succeeded: {report.succeeded}")
    typer.echo(f"Partial: {report.partial}")
    typer.echo(f"Not found: {report.not_found}")
    typer.echo(f"Failed: {report.failed}")

    for item in report.items:
        typer.echo(f"Company ID: {item.company_id}")
        typer.echo(f"Company Name: {item.company_name}")
        typer.echo(f"Status: {item.status}")
        typer.echo(f"Created: {item.created}")
        typer.echo(f"Updated: {item.updated}")
        typer.echo(f"Unchanged: {item.unchanged}")
        typer.echo(f"Changed fields: {', '.join(item.changed_fields) or '(none)'}")
        for error in item.errors:
            typer.echo(f"Error: {error}")

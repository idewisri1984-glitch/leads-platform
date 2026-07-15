from typing import Annotated

import typer

from app.core.database.session import SessionLocal
from app.modules.company_enrichment import (
    CompanyEnrichmentRepository,
    CompanyEnrichmentRunResult,
    CompanyEnrichmentSelectionOptions,
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
    limit: Annotated[int | None, typer.Option(help="Maximum companies to enrich.")] = None,
    only_missing: Annotated[
        bool,
        typer.Option("--only-missing", help="Select companies with missing enrichment fields."),
    ] = False,
    skip_recent_days: Annotated[
        int | None,
        typer.Option(help="Skip companies checked within this many days."),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option(help="Select an exact enrichment status."),
    ] = None,
    company_id: Annotated[
        int | None,
        typer.Option(help="Select one project-scoped company ID."),
    ] = None,
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

    normalized_provider = provider.strip().casefold()
    if normalized_provider not in {"fake", "website"}:
        typer.secho(
            "Unsupported enrichment provider. Choose one of: fake, website.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    if normalized_provider == "website" and limit is None:
        typer.secho(
            "--limit is required when using --provider website.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    effective_limit = 20 if limit is None else limit
    if not 1 <= effective_limit <= 100:
        typer.secho("Limit must be between 1 and 100.", fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        selection_options = CompanyEnrichmentSelectionOptions(
            only_missing=only_missing,
            skip_recent_days=skip_recent_days,
            status=status.upper() if status is not None else None,
            company_id=company_id,
        )
    except ValueError:
        typer.secho(
            "Invalid company enrichment selection options.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from None

    enrichment_provider = _get_enrichment_provider(normalized_provider)

    try:
        with SessionLocal() as session:
            service = CompanyEnrichmentService(CompanyEnrichmentRepository(session))
            report = service.enrich_project_companies(
                project_id=project_id,
                provider=enrichment_provider,
                limit=effective_limit,
                dry_run=dry_run,
                selection_options=selection_options,
            )
    except Exception:
        typer.secho("Company enrichment failed safely.", fg=typer.colors.RED)
        raise typer.Exit(1) from None

    _print_report(
        report,
        project_id=project_id,
        limit=effective_limit,
        persist=persist,
        selection_options=selection_options,
    )


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
    selection_options: CompanyEnrichmentSelectionOptions,
) -> None:
    typer.echo(f"Dry run: {report.dry_run}")
    typer.echo(f"Persistence requested: {persist}")
    typer.echo(f"Provider: {report.provider}")
    typer.echo(f"Project ID: {project_id}")
    typer.echo(f"Limit: {limit}")
    typer.echo(f"Only missing: {selection_options.only_missing}")
    typer.echo(f"Skip recent days: {selection_options.skip_recent_days or '(none)'}")
    typer.echo(
        f"Status filter: {selection_options.status.value if selection_options.status else '(none)'}"
    )
    typer.echo(f"Company ID filter: {selection_options.company_id or '(none)'}")
    typer.echo(f"Matched: {report.matched}")
    typer.echo(f"Skipped by filters: {report.skipped_by_filters}")
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

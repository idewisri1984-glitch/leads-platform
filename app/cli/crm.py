from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from app.modules.crm import CRMOverviewRow
    from app.modules.crm.excel_export import CRMExcelExportResult
    from app.modules.crm.outreach_batch import OutreachBatchResult

app = typer.Typer(help="Show a read-only CRM overview.")

_FAILED = "CRM overview failed."
_HEADERS = (
    "Company ID",
    "Company",
    "Contact ID",
    "Contact",
    "Role",
    "Email",
    "Lead ID",
    "Lead Status",
    "Task ID",
    "Task",
    "Task Status",
    "Draft ID",
    "Draft Task",
    "Draft Status",
    "Outreach Status",
    "Last Sent At",
)


def _load_rows(project_id: int | None, company_id: int | None) -> tuple[CRMOverviewRow, ...]:
    from app.core.database.session import SessionLocal
    from app.modules.crm import CRMOverviewRepository, CRMOverviewService

    with SessionLocal() as session:
        snapshot = CRMOverviewRepository(session).load(project_id, company_id)
        return CRMOverviewService().build(snapshot)


def _export_excel(
    project_id: int | None,
    company_id: int | None,
    output_file: Path,
    *,
    overwrite: bool,
) -> CRMExcelExportResult:
    from app.core.database.session import SessionLocal
    from app.modules.crm.export_execution import execute_crm_excel_export

    return execute_crm_excel_export(
        project_id=project_id,
        company_id=company_id,
        output_file=output_file,
        overwrite=overwrite,
        session_factory=SessionLocal,
    )


def _export_outreach_batch(
    project_id: int,
    email_draft_ids: tuple[int, ...],
    output_file: Path,
    *,
    confirmed: bool,
) -> OutreachBatchResult:
    from app.core.database.session import SessionLocal
    from app.modules.crm.outreach_batch import OutreachBatchWorkflow

    return OutreachBatchWorkflow(SessionLocal).execute(
        project_id=project_id,
        email_draft_ids=email_draft_ids,
        output_file=output_file,
        confirmed=confirmed,
    )


def _json_value(value: datetime | int | str | None) -> datetime | int | str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_dict(row: CRMOverviewRow) -> dict[str, datetime | int | str | None]:
    return {name: _json_value(value) for name, value in row.as_dict().items()}


def _text_value(value: datetime | int | str | None) -> str:
    if value is None:
        return "*"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _render_text(rows: tuple[CRMOverviewRow, ...]) -> None:
    if not rows:
        typer.echo("No CRM records found.")
        return
    console = Console(width=120)
    for position, row in enumerate(rows, start=1):
        table = Table(
            "Field",
            "Value",
            title=f"CRM Record {position}",
            show_lines=True,
            expand=True,
        )
        table.columns[0].no_wrap = True
        for heading, value in zip(_HEADERS, row.as_dict().values(), strict=True):
            table.add_row(heading, Text(_text_value(value)))
        console.print(table)


@app.command("list")
def list_crm(
    project_id: int | None = typer.Option(None, help="Limit results to a Project ID."),
    company_id: int | None = typer.Option(None, help="Limit results to a Company ID."),
    output: str = typer.Option("text", help="Output format: text or json."),
) -> None:
    """List actionable CRM relationships without modifying application data."""
    if project_id is not None and project_id <= 0:
        raise typer.BadParameter("Project ID must be positive.", param_hint="--project-id")
    if company_id is not None and company_id <= 0:
        raise typer.BadParameter("Company ID must be positive.", param_hint="--company-id")
    normalized_output = output.strip().casefold()
    if normalized_output not in {"json", "text"}:
        raise typer.BadParameter("Output must be text or json.", param_hint="--output")
    try:
        rows = _load_rows(project_id, company_id)
    except Exception:
        typer.echo(_FAILED, err=True)
        raise typer.Exit(1) from None
    if normalized_output == "json":
        typer.echo(json.dumps([_row_dict(row) for row in rows], ensure_ascii=True))
        return
    _render_text(rows)


@app.command("export-excel")
def export_excel(
    output_file: Annotated[Path, typer.Option(help="Destination .xlsx file.")],
    project_id: Annotated[int | None, typer.Option(help="Limit results to a Project ID.")] = None,
    company_id: Annotated[int | None, typer.Option(help="Limit results to a Company ID.")] = None,
    overwrite: Annotated[
        bool, typer.Option(help="Replace an existing destination safely.")
    ] = False,
) -> None:
    """Export the read-only CRM overview and supporting records to Excel."""
    if project_id is not None and project_id <= 0:
        raise typer.BadParameter("Project ID must be positive.", param_hint="--project-id")
    if company_id is not None and company_id <= 0:
        raise typer.BadParameter("Company ID must be positive.", param_hint="--company-id")
    try:
        result = _export_excel(
            project_id,
            company_id,
            output_file,
            overwrite=overwrite,
        )
    except Exception as error:
        from app.modules.crm.excel_export import CRMExcelExportError

        message = (
            str(error) if isinstance(error, CRMExcelExportError) else "CRM Excel export failed."
        )
        typer.echo(message, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"CRM Excel export created: {result.output_file}")
    for sheet_name, count in result.counts.items():
        typer.echo(f"{sheet_name}: {count}")


@app.command("export-outreach-batch")
def export_outreach_batch(
    project_id: Annotated[int, typer.Option(help="Project ID for every selected Draft.")],
    email_draft_id: Annotated[
        list[int], typer.Option("--email-draft-id", help="Repeat for each EmailDraft ID.")
    ],
    output_file: Annotated[Path, typer.Option(help="Destination .xlsx file.")],
    confirm: Annotated[
        bool, typer.Option(help="Record the entire batch as manually sent.")
    ] = False,
    output: Annotated[str, typer.Option(help="Output format: text or json.")] = "text",
) -> None:
    """Export selected Drafts and record one confirmed manual-send batch."""
    if project_id <= 0:
        raise typer.BadParameter("Project ID must be positive.", param_hint="--project-id")
    if not email_draft_id or any(value <= 0 for value in email_draft_id):
        raise typer.BadParameter(
            "At least one positive EmailDraft ID is required.",
            param_hint="--email-draft-id",
        )
    normalized_output = output.strip().casefold()
    if normalized_output not in {"json", "text"}:
        raise typer.BadParameter("Output must be text or json.", param_hint="--output")
    try:
        result = _export_outreach_batch(
            project_id,
            tuple(email_draft_id),
            output_file,
            confirmed=confirm,
        )
    except Exception:
        typer.echo("Outreach batch workflow failed.", err=True)
        raise typer.Exit(1) from None
    payload = result.as_dict()
    if normalized_output == "json":
        typer.echo(json.dumps(payload, ensure_ascii=True))
    else:
        typer.echo(f"Status: {payload['status']}")
        typer.echo(f"Project ID: {project_id}")
        typer.echo(f"EmailDraft IDs: {', '.join(map(str, email_draft_id))}")
        typer.echo(f"Output file: {payload['output_file']}")
        if payload["message"]:
            typer.echo(str(payload["message"]))
    if result.status.value not in {"COMPLETE", "CONFIRMATION_REQUIRED"}:
        raise typer.Exit(1)

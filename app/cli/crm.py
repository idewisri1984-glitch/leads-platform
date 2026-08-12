from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from app.modules.crm import CRMOverviewRow

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

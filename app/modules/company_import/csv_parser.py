import csv
from pathlib import Path

from app.modules.company_import.schemas import (
    CompanyImportError,
    CompanyImportResult,
    CompanyImportRow,
)

_OPTIONAL_COLUMNS = ("website", "country", "city", "industry", "notes")


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def parse_company_csv(path: Path) -> CompanyImportResult:
    """
    Parse and validate company rows from a UTF-8 CSV file without writing to the database.
    """

    rows: list[CompanyImportRow] = []
    errors: list[CompanyImportError] = []

    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)

        if reader.fieldnames is None or "name" not in reader.fieldnames:
            return CompanyImportResult(
                rows=[],
                errors=[
                    CompanyImportError(
                        row_number=None,
                        message="Missing required column: name.",
                    )
                ],
            )

        for raw_row in reader:
            row_number = reader.line_num
            normalized_values = [_normalize(value) for value in raw_row.values()]

            if not any(normalized_values):
                continue

            name = _normalize(raw_row.get("name"))

            if name is None:
                errors.append(
                    CompanyImportError(
                        row_number=row_number,
                        message="Company name is required.",
                    )
                )
                continue

            optional_values = {
                column: _normalize(raw_row.get(column)) for column in _OPTIONAL_COLUMNS
            }
            status = _normalize(raw_row.get("status")) or "NEW"
            rows.append(
                CompanyImportRow(
                    row_number=row_number,
                    name=name,
                    status=status,
                    **optional_values,
                )
            )

    return CompanyImportResult(rows=rows, errors=errors)

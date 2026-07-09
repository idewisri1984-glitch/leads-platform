from pathlib import Path

from sqlalchemy.orm import Session

from app.modules.company_import.csv_parser import parse_company_csv
from app.modules.company_import.ingestion import CompanyIngestionService
from app.modules.company_import.schemas import (
    CompanyImportRow,
    CompanyIngestionError,
    CompanyIngestionItem,
    CompanyIngestionResult,
)


def company_import_row_to_ingestion_item(
    row: CompanyImportRow,
) -> CompanyIngestionItem:
    """
    Adapt one validated CSV row to source-independent ingestion data.
    """

    return CompanyIngestionItem(
        source_row_number=row.row_number,
        name=row.name,
        website=row.website,
        country=row.country,
        city=row.city,
        industry=row.industry,
        status=row.status,
        notes=row.notes,
    )


def ingest_company_csv(
    session: Session,
    project_id: int,
    path: Path,
) -> CompanyIngestionResult:
    """
    Parse a CSV file and persist its valid rows through the shared ingestion service.
    """

    parsed = parse_company_csv(path)
    parser_errors = [
        CompanyIngestionError(
            source_row_number=error.row_number,
            code="csv_validation_error",
            message=error.message,
        )
        for error in parsed.errors
    ]
    items = [company_import_row_to_ingestion_item(row) for row in parsed.rows]

    if not items:
        return CompanyIngestionResult(
            total_rows=len(parser_errors),
            imported=0,
            skipped_duplicates=0,
            failed=len(parser_errors),
            created_company_ids=[],
            duplicates=[],
            errors=parser_errors,
            rolled_back=False,
        )

    ingestion_result = CompanyIngestionService(session).ingest(project_id, items)

    return CompanyIngestionResult(
        total_rows=len(parser_errors) + ingestion_result.total_rows,
        imported=ingestion_result.imported,
        skipped_duplicates=ingestion_result.skipped_duplicates,
        failed=len(parser_errors) + ingestion_result.failed,
        created_company_ids=ingestion_result.created_company_ids,
        duplicates=ingestion_result.duplicates,
        errors=[*parser_errors, *ingestion_result.errors],
        rolled_back=ingestion_result.rolled_back,
    )

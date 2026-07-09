from pydantic import BaseModel


class CompanyImportRow(BaseModel):
    """
    Validated company data from one CSV row.
    """

    row_number: int
    name: str
    website: str | None
    country: str | None
    city: str | None
    industry: str | None
    status: str
    notes: str | None


class CompanyImportError(BaseModel):
    """
    Validation error associated with a CSV row or the file header.
    """

    row_number: int | None
    message: str


class CompanyImportResult(BaseModel):
    """
    Dry-run result containing valid rows and validation errors.
    """

    rows: list[CompanyImportRow]
    errors: list[CompanyImportError]


class CompanyIngestionItem(BaseModel):
    """
    Source-independent company data accepted by the future ingestion service.
    """

    source_row_number: int | None = None
    name: str
    website: str | None = None
    country: str | None = None
    city: str | None = None
    industry: str | None = None
    status: str = "NEW"
    notes: str | None = None


class CompanyIngestionDuplicate(BaseModel):
    """
    Company skipped because it matched an existing ingestion identity.
    """

    source_row_number: int | None
    existing_company_id: int
    matched_by: str
    matched_value: str


class CompanyIngestionError(BaseModel):
    """
    Error encountered while ingesting one company or an entire batch.
    """

    source_row_number: int | None
    code: str
    message: str


class CompanyIngestionResult(BaseModel):
    """
    Persistence result returned by the future ingestion service.
    """

    total_rows: int
    imported: int
    skipped_duplicates: int
    failed: int
    created_company_ids: list[int]
    duplicates: list[CompanyIngestionDuplicate]
    errors: list[CompanyIngestionError]
    rolled_back: bool

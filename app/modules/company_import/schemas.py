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

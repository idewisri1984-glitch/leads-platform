from app.modules.company_discovery.schemas import CompanyDiscoveryRequest
from app.modules.company_import.schemas import CompanyIngestionItem
from app.providers.serpapi.schemas import SerpApiCompanyResult

_MAX_NOTES_LENGTH = 240


class CompanyDiscoveryAdapterError(ValueError):
    """
    Provider result could not be adapted into source-independent company data.
    """


def serpapi_result_to_ingestion_item(
    result: SerpApiCompanyResult,
    request: CompanyDiscoveryRequest,
) -> CompanyIngestionItem:
    """
    Adapt one SerpAPI organic result into source-independent company ingestion data.
    """

    name = result.title.strip()

    if not name:
        raise CompanyDiscoveryAdapterError("Discovery result title is required.")

    return CompanyIngestionItem(
        source_row_number=result.position,
        name=name,
        website=result.link,
        country=request.country,
        city=request.city,
        industry=request.industry,
        status="NEW",
        notes=_safe_notes(result.snippet),
    )


def _safe_notes(snippet: str | None) -> str | None:
    if snippet is None:
        return None

    normalized = " ".join(snippet.strip().split())

    if not normalized:
        return None

    notes = f"Discovered via SerpAPI: {normalized}"

    if len(notes) <= _MAX_NOTES_LENGTH:
        return notes

    return notes[: _MAX_NOTES_LENGTH - 3].rstrip() + "..."

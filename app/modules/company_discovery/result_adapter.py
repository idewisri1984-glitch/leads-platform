from urllib.parse import urlsplit

from pydantic import ValidationError

from app.modules.company_discovery.schemas import DiscoveryProviderResult
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.schemas import SearchQuery

_MAX_NOTES_LENGTH = 1000
_UNSAFE_NOTE_MARKERS = (
    "api key",
    "api-key",
    "api_key",
    "apikey",
    "database url",
    "database_url",
    "private key",
    "private_key",
    "raw json",
    "raw payload",
    "secret",
    "settings repr",
)


class DiscoveryResultAdapterError(Exception):
    """Controlled failure while adapting provider result to ingestion item."""


def provider_result_to_ingestion_item(
    result: DiscoveryProviderResult,
    *,
    query: SearchQuery,
    provider_name: str,
) -> CompanyIngestionItem:
    name = _required_text(result.title, "Discovery result title is required.")
    normalized_provider = _required_text(
        provider_name,
        "Discovery provider name is required.",
    )

    try:
        return CompanyIngestionItem(
            source_row_number=result.position,
            name=name,
            website=result.link,
            country=query.country,
            city=query.city,
            industry=None,
            status="NEW",
            notes=_build_notes(
                provider_name=normalized_provider,
                query_text=query.text,
                source=result.source,
                snippet=result.snippet,
            ),
        )
    except ValidationError:
        raise DiscoveryResultAdapterError("Discovery result could not be adapted.") from None


def _required_text(value: object, error_message: str) -> str:
    if not isinstance(value, str):
        raise DiscoveryResultAdapterError(error_message)

    normalized = _normalize_whitespace(value)

    if not normalized:
        raise DiscoveryResultAdapterError(error_message)

    return normalized


def _build_notes(
    *,
    provider_name: str,
    query_text: str,
    source: str | None,
    snippet: str | None,
) -> str:
    parts = [f"Provider: {provider_name}"]
    safe_query = _safe_note_value(query_text)
    safe_source = _safe_note_value(source)
    safe_snippet = _safe_note_value(snippet)

    if safe_query is not None:
        parts.append(f"Query: {safe_query}")

    if safe_source is not None:
        parts.append(f"Source: {safe_source}")

    if safe_snippet is not None:
        parts.append(f"Snippet: {safe_snippet}")

    notes = "; ".join(parts)

    if len(notes) <= _MAX_NOTES_LENGTH:
        return notes

    return notes[: _MAX_NOTES_LENGTH - 3].rstrip() + "..."


def _safe_note_value(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = _normalize_whitespace(value)

    if not normalized:
        return None

    lowered = normalized.casefold()

    if any(marker in lowered for marker in _UNSAFE_NOTE_MARKERS):
        return None

    for token in normalized.split():
        if "://" not in token:
            continue

        parsed = urlsplit(token)

        if parsed.username is not None or parsed.password is not None:
            return None

    return normalized


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.strip().split())

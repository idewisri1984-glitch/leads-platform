from pydantic import ValidationError

from app.modules.company_discovery.staging_normalization import (
    NormalizedCompanyDiscoveryCandidate,
    normalize_candidate_identity,
)
from app.modules.company_discovery.staging_schemas import CompanyDiscoveryCandidateCreate
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidateDraft,
)
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.schemas import SearchQuery


class CompanyDiscoveryStagingAdapterError(ValueError):
    """Controlled staging adaptation failure."""


_SAFE_ADAPTER_ERROR_MESSAGE = "Company discovery candidate could not be staged."


def _safe_position(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def adapt_item_to_candidate_draft(
    *,
    project_id: int,
    provider: str,
    query: SearchQuery,
    item: CompanyIngestionItem,
) -> tuple[CompanyDiscoveryStagingCandidateDraft, NormalizedCompanyDiscoveryCandidate]:
    if provider is None or not str(provider).strip():
        raise CompanyDiscoveryStagingAdapterError(_SAFE_ADAPTER_ERROR_MESSAGE)

    try:
        draft = CompanyDiscoveryStagingCandidateDraft(
            project_id=project_id,
            provider=provider,
            name=item.name,
            website=item.website,
            country_code=query.country_code,
            position=_safe_position(item.source_row_number),
        )
    except ValidationError as error:
        raise CompanyDiscoveryStagingAdapterError(_SAFE_ADAPTER_ERROR_MESSAGE) from error

    try:
        normalized = normalize_candidate_identity(
            name=draft.name,
            website=draft.website,
            country_code=draft.country_code,
        )
    except (ValueError, TypeError) as error:
        raise CompanyDiscoveryStagingAdapterError(_SAFE_ADAPTER_ERROR_MESSAGE) from error

    return draft, normalized


def adapt_query_items(
    *,
    project_id: int,
    provider: str,
    query: SearchQuery,
    items: list[CompanyIngestionItem],
) -> tuple[
    list[tuple[CompanyDiscoveryStagingCandidateDraft, NormalizedCompanyDiscoveryCandidate]],
    int,
]:
    drafts: list[
        tuple[CompanyDiscoveryStagingCandidateDraft, NormalizedCompanyDiscoveryCandidate]
    ] = []
    rejected = 0

    for item in items:
        try:
            drafts.append(
                adapt_item_to_candidate_draft(
                    project_id=project_id,
                    provider=provider,
                    query=query,
                    item=item,
                )
            )
        except CompanyDiscoveryStagingAdapterError:
            rejected += 1

    return drafts, rejected


def candidate_create_from_adapter_payload(
    *,
    draft: CompanyDiscoveryStagingCandidateDraft,
    run_id: int,
    normalized: NormalizedCompanyDiscoveryCandidate,
) -> CompanyDiscoveryCandidateCreate:
    return CompanyDiscoveryCandidateCreate(
        project_id=draft.project_id,
        run_id=run_id,
        provider=draft.provider,
        name=draft.name,
        website=normalized.website,
        country_code=normalized.country_code,
        position=draft.position,
    )

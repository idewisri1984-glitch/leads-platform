import pytest

from app.modules.company_discovery.staging_adapter import (
    CompanyDiscoveryStagingAdapterError,
    adapt_item_to_candidate_draft,
    adapt_query_items,
    candidate_create_from_adapter_payload,
)
from app.modules.company_discovery.staging_normalization import NormalizedCompanyDiscoveryCandidate
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidateDraft,
)
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.schemas import SearchQuery


def make_query(*, country_code: str | None = "US") -> SearchQuery:
    return SearchQuery(
        text="demo query",
        profile_id=1,
        profile_name="Demo profile",
        country="United States" if country_code == "US" else None,
        country_code=country_code,
        source_template="{target_customer_type} {city} {country}",
        city=None,
        language=None,
        limit=10,
    )


def make_item(
    *, name: str | None, website: str | None, row_number: int, country: str | None = "US"
) -> CompanyIngestionItem:
    return CompanyIngestionItem(
        source_row_number=row_number,
        name=name,
        website=website,
        country=country,
        city=None,
    )


def test_adapt_query_items_prefers_query_country_code() -> None:
    query = make_query(country_code="GB")
    item = make_item(name="Acme Ltd", website=None, row_number=1)
    rows, rejected = adapt_query_items(
        project_id=123,
        provider="serpapi",
        query=query,
        items=[item],
    )

    assert rejected == 0
    assert len(rows) == 1
    draft, normalized = rows[0]
    assert isinstance(draft, CompanyDiscoveryStagingCandidateDraft)
    assert isinstance(normalized, NormalizedCompanyDiscoveryCandidate)
    assert draft.country_code == "GB"
    assert normalized.identity_key == "name_country:acme ltd|GB"


def test_adapt_item_with_website_keeps_country_optional() -> None:
    query = make_query(country_code=None)
    item = make_item(name="Acme", website="https://www.example.com/about", row_number=2)
    rows, rejected = adapt_query_items(
        project_id=1,
        provider="serpapi",
        query=query,
        items=[item],
    )

    assert rejected == 0
    assert len(rows) == 1
    draft, normalized = rows[0]
    assert draft.country_code is None
    assert draft.position == 2
    assert normalized.identity_key == "website:example.com"


def test_invalid_website_is_rejected_and_counted() -> None:
    query = make_query(country_code="US")
    valid = make_item(name="Good", website="https://example.com", row_number=1)
    invalid = make_item(name="Bad", website="not-a-url", row_number=2)
    rows, rejected = adapt_query_items(
        project_id=5,
        provider="serpapi",
        query=query,
        items=[valid, invalid],
    )

    assert rejected == 1
    assert len(rows) == 1
    assert rows[0][0].name == "Good"


def test_markup_is_rejected_by_draft_validation() -> None:
    query = make_query(country_code="US")
    markup = make_item(name="Acme <b>Corp</b>", website="https://example.com", row_number=3)
    _, rejected = adapt_query_items(
        project_id=9,
        provider="serpapi",
        query=query,
        items=[markup],
    )

    assert rejected == 1


@pytest.mark.parametrize(
    "name",
    [
        "<Company>",
        "Company>",
        "<Company",
        "  <Acme Corp>  ",
    ],
)
def test_markup_variants_in_candidate_name_are_rejected(name: str) -> None:
    query = make_query(country_code="US")
    rows, rejected = adapt_query_items(
        project_id=2,
        provider="serpapi",
        query=query,
        items=[make_item(name=name, website="https://example.com", row_number=10)],
    )

    assert rejected == 1
    assert rows == []


def test_name_with_ampersand_is_accepted_without_markup() -> None:
    query = make_query(country_code="US")
    rows, rejected = adapt_query_items(
        project_id=3,
        provider="serpapi",
        query=query,
        items=[
            make_item(
                name="Smith & Co.",
                website="https://example.com",
                row_number=5,
            )
        ],
    )

    assert rejected == 0
    assert len(rows) == 1
    assert rows[0][0].name == "Smith & Co."


def test_name_with_slash_and_parentheses_is_accepted() -> None:
    query = make_query(country_code="US")
    rows, rejected = adapt_query_items(
        project_id=4,
        provider="serpapi",
        query=query,
        items=[
            make_item(
                name="A/B Studio (Berlin)",
                website="https://example.com",
                row_number=6,
            )
        ],
    )

    assert rejected == 0
    assert len(rows) == 1
    assert rows[0][0].name == "A/B Studio (Berlin)"


def test_valid_https_url_with_query_params_is_accepted() -> None:
    query = make_query(country_code="US")
    rows, rejected = adapt_query_items(
        project_id=5,
        provider="serpapi",
        query=query,
        items=[
            make_item(
                name="Acme",
                website="https://example.com/search?q=design&source=stage#results",
                row_number=7,
            )
        ],
    )

    assert rejected == 0
    assert len(rows) == 1
    assert rows[0][1].website is not None
    assert rows[0][1].website.startswith("https://example.com/search")


def test_fallback_without_country_and_without_website_is_rejected() -> None:
    query = make_query(country_code=None)
    item = make_item(name="Nameless", website=None, row_number=4, country=None)
    _, rejected = adapt_query_items(
        project_id=1,
        provider="serpapi",
        query=query,
        items=[item],
    )

    assert rejected == 1


def test_adapted_rows_are_stable_when_no_items() -> None:
    rows, rejected = adapt_query_items(
        project_id=1,
        provider="serpapi",
        query=make_query(),
        items=[],
    )

    assert rows == []
    assert rejected == 0


def test_candidate_position_is_normalized_to_none_when_invalid() -> None:
    query = make_query(country_code="US")
    zero = make_item(name="Zero", website="https://example.com", row_number=0)
    row, rejected = adapt_query_items(
        project_id=1,
        provider="serpapi",
        query=query,
        items=[zero],
    )

    assert rejected == 0
    assert len(row) == 1
    assert row[0][0].position is None


def test_candidate_create_payload_uses_normalized_payload_values() -> None:
    query = make_query(country_code="US")
    item = make_item(name="  Demo   Co.  ", website="https://EXAMPLE.COM/path", row_number=1)
    rows, rejected = adapt_query_items(
        project_id=10,
        provider="serpapi",
        query=query,
        items=[item],
    )
    draft, normalized = rows[0]

    payload = candidate_create_from_adapter_payload(
        draft=draft,
        run_id=999,
        normalized=normalized,
    )

    assert rejected == 0
    assert payload.project_id == 10
    assert payload.run_id == 999
    assert payload.name == "Demo Co."
    assert payload.website == "https://example.com/path"
    assert payload.country_code == "US"
    assert payload.position == 1


def test_adapter_failure_raises_controlled_error() -> None:
    query = make_query(country_code="US")

    with pytest.raises(CompanyDiscoveryStagingAdapterError):
        adapt_item_to_candidate_draft(
            project_id=1,
            provider="",
            query=query,
            item=make_item(name="Acme", website="https://example.com", row_number=1),
        )

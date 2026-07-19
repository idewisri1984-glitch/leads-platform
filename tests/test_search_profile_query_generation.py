from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from app.modules.search_profile import (
    SearchProfileQueryGenerationError,
    SearchProfileQueryGenerator,
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)


class GuardedVirtualList(list[str]):
    def __init__(self, prefix: str, size: int, max_yields: int) -> None:
        super().__init__()
        self.prefix = prefix
        self.size = size
        self.max_yields = max_yields
        self.yield_count = 0

    def __bool__(self) -> bool:
        return self.size > 0

    def __len__(self) -> int:
        return self.size

    def __iter__(self) -> Iterator[str]:
        for index in range(self.size):
            self.yield_count += 1
            if self.yield_count > self.max_yields:
                raise AssertionError("Geography iterable was consumed eagerly.")
            yield f"{self.prefix}{index}"


def make_profile(
    *,
    profile_id: int = 1,
    name: str = "Universal profile",
    product_or_service: str = "business service",
    target_customer_types: list[str] | None = None,
    target_industries: list[str] | None = None,
    positive_keywords: list[str] | None = None,
    negative_keywords: list[str] | None = None,
    countries: list[str] | None = None,
    cities: list[str] | None = None,
    languages: list[str] | None = None,
    query_templates: list[str] | None = None,
    result_limit: int = 10,
    max_queries_per_run: int = 10,
    total_result_ceiling: int = 100,
) -> SearchProfileRead:
    return SearchProfileRead(
        id=profile_id,
        project_id=1,
        name=name,
        description=None,
        product_or_service=product_or_service,
        target_customer_types=target_customer_types or ["buyers"],
        target_industries=target_industries or [],
        positive_keywords=positive_keywords or [],
        negative_keywords=negative_keywords or [],
        countries=countries or [],
        cities=cities or [],
        languages=languages or [],
        query_templates=query_templates or [],
        result_limit=result_limit,
        max_queries_per_run=max_queries_per_run,
        total_result_ceiling=total_result_ceiling,
        enabled=True,
    )


def generate(
    profile: SearchProfileRead,
    options: SearchProfileRunOptions | None = None,
) -> SearchQueryPreview:
    return SearchProfileQueryGenerator().generate_preview(profile, options)


def test_universal_business_cases_generate_without_niche_specific_logic() -> None:
    profiles = [
        make_profile(
            name="Furniture buyers",
            product_or_service="handcrafted furniture",
            target_customer_types=["interior designers"],
            countries=["USA"],
        ),
        make_profile(
            name="Accounting SaaS",
            product_or_service="accounting SaaS",
            target_customer_types=["small businesses"],
            countries=["Germany"],
        ),
        make_profile(
            name="Industrial pumps",
            product_or_service="industrial pumps",
            target_customer_types=["distributors"],
            countries=["UAE"],
        ),
        make_profile(
            name="Recruitment services",
            product_or_service="recruitment services",
            target_customer_types=["technology companies"],
            countries=["Singapore"],
        ),
        make_profile(
            name="Commercial cleaning",
            product_or_service="commercial cleaning",
            target_customer_types=["property managers"],
            countries=["Australia"],
        ),
    ]

    query_texts = [generate(profile).queries[0].text for profile in profiles]

    assert query_texts == [
        "interior designers USA",
        "small businesses Germany",
        "distributors UAE",
        "technology companies Singapore",
        "property managers Australia",
    ]


def test_default_templates_are_used_without_mutating_profile() -> None:
    profile = make_profile(target_customer_types=["accounting firms"], countries=["Germany"])

    preview = generate(profile)

    assert preview.queries[0].source_template == "{target_customer_type} {city} {country}"
    assert preview.queries[0].text == "accounting firms Germany"
    assert profile.query_templates == []


def test_no_override_uses_stored_free_text_country_values() -> None:
    profile = make_profile(
        target_customer_types=["accounting firms"],
        countries=["Indonesia", "United States", "US"],
    )

    preview = generate(profile)

    assert preview.queries[0].text == "accounting firms Indonesia"
    assert preview.queries[0].country == "Indonesia"
    assert preview.queries[0].country_code is None


def test_explicit_country_codes_override_displays_canonical_names_and_codes() -> None:
    profile = make_profile(
        target_customer_types=["accounting firms"],
        cities=["Berlin"],
        countries=["Indonesia", "Australia"],
        query_templates=["{target_customer_type} {city} {country}"],
    )
    preview = generate(
        profile,
        SearchProfileRunOptions(country_codes=("gb", " DE ", "US")),
    )

    assert [query.text for query in preview.queries] == [
        "accounting firms Berlin Germany",
        "accounting firms Berlin United Kingdom",
        "accounting firms Berlin United States",
    ]
    assert [query.country_code for query in preview.queries] == ["DE", "GB", "US"]
    assert preview.queries[0].country == "Germany"


def test_custom_query_templates_are_used() -> None:
    profile = make_profile(
        product_or_service="industrial pumps",
        target_customer_types=["distributors"],
        countries=["UAE"],
        query_templates=["{product_or_service} {target_customer_type} {country}"],
    )

    preview = generate(profile)

    assert preview.queries[0].text == "industrial pumps distributors UAE"
    assert preview.queries[0].source_template == (
        "{product_or_service} {target_customer_type} {country}"
    )


def test_global_profile_with_no_geography() -> None:
    profile = make_profile(target_customer_types=["technology startups"])

    preview = generate(profile)

    assert preview.queries[0].text == "technology startups"
    assert preview.queries[0].city is None
    assert preview.queries[0].country is None


def test_countries_only() -> None:
    profile = make_profile(target_customer_types=["accounting firms"], countries=["Germany"])

    assert generate(profile).queries[0].text == "accounting firms Germany"


def test_cities_only() -> None:
    profile = make_profile(target_customer_types=["property managers"], cities=["Sydney"])

    preview = generate(profile)

    assert preview.queries[0].text == "property managers Sydney"
    assert preview.queries[0].city == "Sydney"
    assert preview.queries[0].country is None


def test_countries_only_uses_free_text_country_code_none() -> None:
    profile = make_profile(target_customer_types=["accounting firms"], countries=["US"])

    preview = generate(profile)

    assert preview.queries[0].country == "US"
    assert preview.queries[0].country_code is None


def test_cities_and_profile_countries_cross_product_stays_legacy() -> None:
    profile = make_profile(
        target_customer_types=["buyers"],
        cities=["Berlin", "Paris"],
        countries=["Indonesia", "US"],
        query_templates=["{target_customer_type} {city} {country}"],
    )

    assert [query.text for query in generate(profile).queries] == [
        "buyers Berlin Indonesia",
        "buyers Berlin US",
        "buyers Paris Indonesia",
        "buyers Paris US",
    ]


def test_cities_and_countries_deterministic_ordering() -> None:
    profile = make_profile(
        target_customer_types=["distributors"],
        cities=["Dubai", "Abu Dhabi"],
        countries=["UAE", "Saudi Arabia"],
        max_queries_per_run=4,
    )

    assert [query.text for query in generate(profile).queries] == [
        "distributors Dubai UAE",
        "distributors Dubai Saudi Arabia",
        "distributors Abu Dhabi UAE",
        "distributors Abu Dhabi Saudi Arabia",
    ]


def test_large_geography_product_is_consumed_lazily() -> None:
    profile = make_profile(
        target_customer_types=["buyers"],
        query_templates=["{target_customer_type} {city} {country}"],
        max_queries_per_run=1,
    )
    cities = GuardedVirtualList("city", size=10_000, max_yields=1)
    countries = GuardedVirtualList("country", size=10_000, max_yields=1)
    profile.cities = cities
    profile.countries = countries

    preview = generate(profile)

    assert [query.text for query in preview.queries] == ["buyers city0 country0"]
    assert cities.yield_count == 1
    assert countries.yield_count == 1


def test_geography_iterator_is_fresh_for_each_audience_value() -> None:
    profile = make_profile(
        target_customer_types=["buyers", "sellers"],
        cities=["Sydney", "Melbourne"],
        countries=["Australia"],
        query_templates=["{target_customer_type} {city} {country}"],
        max_queries_per_run=4,
    )

    assert [query.text for query in generate(profile).queries] == [
        "buyers Sydney Australia",
        "buyers Melbourne Australia",
        "sellers Sydney Australia",
        "sellers Melbourne Australia",
    ]


def test_total_result_ceiling_stops_lazy_geography_iteration() -> None:
    profile = make_profile(
        target_customer_types=["buyers"],
        cities=["Sydney"],
        countries=["Australia", "Germany", "USA"],
        query_templates=["{target_customer_type} {city} {country}"],
        result_limit=10,
        max_queries_per_run=10,
        total_result_ceiling=15,
    )

    preview = generate(profile)

    assert [query.text for query in preview.queries] == [
        "buyers Sydney Australia",
        "buyers Sydney Germany",
    ]
    assert [query.limit for query in preview.queries] == [10, 5]


def test_customer_types_have_priority_before_industries_and_keywords() -> None:
    profile = make_profile(
        target_customer_types=["hotels"],
        target_industries=["hospitality"],
        positive_keywords=["commercial property"],
        countries=["Australia"],
    )

    preview = generate(profile)

    assert preview.queries[0].text == "hotels Australia"
    assert "hospitality companies Australia" in [query.text for query in preview.queries]
    assert "commercial property Australia" in [query.text for query in preview.queries]


def test_max_queries_per_run_is_enforced() -> None:
    profile = make_profile(
        target_customer_types=["offices", "hotels", "malls"],
        countries=["Australia"],
        max_queries_per_run=2,
    )

    preview = generate(profile)

    assert preview.query_count == 2
    assert [query.text for query in preview.queries] == ["offices Australia", "hotels Australia"]


def test_options_may_lower_max_queries_but_not_raise_it() -> None:
    profile = make_profile(
        target_customer_types=["one", "two", "three"],
        countries=["USA"],
        max_queries_per_run=2,
    )

    lowered = generate(profile, SearchProfileRunOptions(max_queries=1))
    raised = generate(profile, SearchProfileRunOptions(max_queries=10))

    assert lowered.query_count == 1
    assert raised.query_count == 2


def test_result_limit_override_may_lower_but_not_raise_profile_value() -> None:
    profile = make_profile(result_limit=20)

    lowered = generate(profile, SearchProfileRunOptions(result_limit_per_query=5))
    raised = generate(profile, SearchProfileRunOptions(result_limit_per_query=50))

    assert lowered.result_limit_per_query == 5
    assert lowered.queries[0].limit == 5
    assert raised.result_limit_per_query == 20
    assert raised.queries[0].limit == 20


def test_total_result_ceiling_override_may_lower_but_not_raise_profile_value() -> None:
    profile = make_profile(
        target_customer_types=[str(index) for index in range(30)],
        query_templates=["{target_customer_type}"],
        max_queries_per_run=30,
        result_limit=10,
        total_result_ceiling=200,
    )

    lowered = generate(profile, SearchProfileRunOptions(total_result_ceiling=50))
    raised = generate(profile, SearchProfileRunOptions(total_result_ceiling=500))

    assert lowered.total_result_ceiling == 50
    assert sum(query.limit for query in lowered.queries) == 50
    assert raised.total_result_ceiling == 200
    assert sum(query.limit for query in raised.queries) == 200


def test_total_result_ceiling_reduces_final_query_limit() -> None:
    profile = make_profile(
        target_customer_types=["one", "two", "three"],
        query_templates=["{target_customer_type}"],
        result_limit=10,
        max_queries_per_run=10,
        total_result_ceiling=15,
    )

    preview = generate(profile)

    assert [query.text for query in preview.queries] == ["one", "two"]
    assert [query.limit for query in preview.queries] == [10, 5]
    assert sum(query.limit for query in preview.queries) <= preview.total_result_ceiling


def test_ceiling_smaller_than_result_limit_produces_one_reduced_query() -> None:
    profile = make_profile(
        target_customer_types=["one", "two"],
        query_templates=["{target_customer_type}"],
        result_limit=10,
        max_queries_per_run=10,
        total_result_ceiling=5,
    )

    preview = generate(profile)

    assert [query.text for query in preview.queries] == ["one"]
    assert [query.limit for query in preview.queries] == [5]
    assert preview.result_limit_per_query == 10


def test_max_queries_remains_more_restrictive_than_result_ceiling() -> None:
    profile = make_profile(
        target_customer_types=["one", "two", "three"],
        query_templates=["{target_customer_type}"],
        result_limit=10,
        max_queries_per_run=2,
        total_result_ceiling=100,
    )

    preview = generate(profile)

    assert [query.text for query in preview.queries] == ["one", "two"]
    assert [query.limit for query in preview.queries] == [10, 10]


def test_run_option_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        SearchProfileRunOptions(max_queries=0)

    with pytest.raises(ValidationError):
        SearchProfileRunOptions(result_limit_per_query=101)

    with pytest.raises(ValidationError):
        SearchProfileRunOptions(total_result_ceiling=1001)


def test_search_query_accepts_country_code_none_and_rejects_unknown_types() -> None:
    profile = make_profile()
    assert generate(profile).queries[0].country_code is None

    with pytest.raises(ValidationError):
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code=123,
            limit=10,
        )


def test_country_code_in_search_query_accepts_none_and_canonicalizes_input() -> None:
    assert (
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code=None,
            limit=10,
        ).country_code
        is None
    )

    assert (
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code="us",
            limit=10,
        ).country_code
        == "US"
    )

    assert (
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code=" de ",
            limit=10,
        ).country_code
        == "DE"
    )

    assert (
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code="gb",
            limit=10,
        ).country_code
        == "GB"
    )

    assert (
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code="ID",
            limit=10,
        ).country_code
        == "ID"
    )


@pytest.mark.parametrize(
    "value",
    [
        "UK",
        "ZZ",
        "SU",
        "",
        "   ",
        "United States",
        "Germany",
        "not-an-code",
        True,
        123,
        object(),
    ],
)
def test_search_query_rejects_invalid_country_code_values(value: object) -> None:
    with pytest.raises(ValidationError):
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code=value,
            limit=10,
        )


def test_search_query_rejects_markup_country_code() -> None:
    with pytest.raises(ValidationError):
        SearchQuery(
            text="sample",
            profile_id=1,
            profile_name="Profile",
            country="US",
            city=None,
            source_template="{target_customer_type} {country}",
            country_code="<US>",
            limit=10,
        )


def test_country_codes_validation_rejects_plain_string_and_empty_collection() -> None:
    with pytest.raises(ValidationError):
        SearchProfileRunOptions(country_codes="US")

    with pytest.raises(ValidationError):
        SearchProfileRunOptions(country_codes=[])


def test_country_codes_validation_rejects_unknown_or_invalid_codes() -> None:
    with pytest.raises(ValidationError):
        SearchProfileRunOptions(country_codes=("ZZ",))

    with pytest.raises(ValidationError):
        SearchProfileRunOptions(country_codes=("UK",))


def test_country_codes_validation_limits_collection_size() -> None:
    codes = [
        "US",
        "DE",
        "FR",
        "GB",
        "ID",
        "CA",
        "AU",
        "JP",
        "CN",
        "IN",
        "ES",
        "IT",
        "BR",
        "MX",
        "RU",
        "NL",
        "SE",
        "NO",
        "FI",
        "DK",
    ]
    SearchProfileRunOptions(country_codes=codes)

    codes = [
        "US",
        "DE",
        "FR",
        "GB",
        "ID",
        "CA",
        "AU",
        "JP",
        "CN",
        "IN",
        "ES",
        "IT",
        "BR",
        "MX",
        "RU",
        "NL",
        "SE",
        "NO",
        "FI",
        "DK",
        "AR",
    ]
    with pytest.raises(ValidationError):
        SearchProfileRunOptions(country_codes=codes)


def test_negative_single_keyword_syntax() -> None:
    profile = make_profile(target_customer_types=["accounting firms"], negative_keywords=["jobs"])

    assert generate(profile).queries[0].text == "accounting firms -jobs"


def test_negative_multi_word_phrase_syntax() -> None:
    profile = make_profile(
        target_customer_types=["accounting firms"],
        negative_keywords=["free template"],
    )

    assert generate(profile).queries[0].text == 'accounting firms -"free template"'


def test_duplicate_negative_keywords_are_removed() -> None:
    profile = make_profile(
        target_customer_types=["accounting firms"],
        negative_keywords=["Jobs", "jobs", "free template", "Free Template"],
    )

    assert generate(profile).queries[0].text == 'accounting firms -Jobs -"free template"'


def test_whitespace_normalization() -> None:
    profile = make_profile(
        target_customer_types=["accounting firms"],
        countries=["Germany"],
        query_templates=["  {target_customer_type}    {country}  "],
    )

    assert generate(profile).queries[0].text == "accounting firms Germany"


def test_preserves_explicit_user_quotes() -> None:
    profile = make_profile(
        target_customer_types=['"accounting firms"'],
        countries=["Germany"],
    )

    assert generate(profile).queries[0].text == '"accounting firms" Germany'


def test_duplicate_generated_queries_removed_case_insensitively() -> None:
    profile = make_profile(
        target_customer_types=["Hotels", "hotels"],
        countries=["USA"],
        query_templates=["{target_customer_type} {country}"],
    )

    preview = generate(profile)

    assert [query.text for query in preview.queries] == ["Hotels USA"]


def test_language_variants_only_when_template_contains_language() -> None:
    profile = make_profile(
        target_customer_types=["startups"],
        countries=["Singapore"],
        languages=["en", "de"],
        query_templates=[
            "{target_customer_type} {country}",
            "{target_customer_type} {language} {country}",
        ],
    )

    preview = generate(profile)

    assert [query.text for query in preview.queries] == [
        "startups Singapore",
        "startups en Singapore",
        "startups de Singapore",
    ]
    assert [query.language for query in preview.queries] == [None, "en", "de"]


def test_override_country_codes_are_sorted_deduplicated_and_city_cross_product() -> None:
    profile = make_profile(
        target_customer_types=["buyers"],
        cities=["Berlin", "Paris"],
        countries=["Australia"],
        query_templates=["{target_customer_type} {city} {country}"],
    )

    preview = generate(
        profile,
        SearchProfileRunOptions(country_codes=("US", "gb ", "DE", " us ")),
    )

    assert [query.text for query in preview.queries] == [
        "buyers Berlin Germany",
        "buyers Paris Germany",
        "buyers Berlin United Kingdom",
        "buyers Paris United Kingdom",
        "buyers Berlin United States",
        "buyers Paris United States",
    ]
    assert [query.country_code for query in preview.queries] == ["DE", "DE", "GB", "GB", "US", "US"]


def test_override_does_not_mutate_profile() -> None:
    profile = make_profile(
        target_customer_types=["accounting firms"],
        cities=["Berlin"],
        countries=["Australia", "Indonesia"],
    )
    original = profile.model_dump()

    generate(profile, SearchProfileRunOptions(country_codes=("US", "DE")))

    assert profile.model_dump() == original


def test_unknown_placeholder_raises_controlled_error() -> None:
    profile = make_profile(query_templates=["{target_customer_type} {unsupported}"])

    with pytest.raises(SearchProfileQueryGenerationError, match="Unsupported"):
        generate(profile)


def test_template_with_multiple_audience_placeholders_raises_controlled_error() -> None:
    profile = make_profile(
        query_templates=["{target_customer_type} {target_industry} {country}"],
        target_industries=["manufacturing"],
    )

    with pytest.raises(SearchProfileQueryGenerationError, match="multiple audience"):
        generate(profile)


def test_empty_rendered_query_is_skipped() -> None:
    profile = make_profile(query_templates=["{city}"])

    preview = generate(profile)

    assert preview.queries == []
    assert preview.query_count == 0
    assert preview.estimated_provider_requests == 0


def test_query_metadata_is_correct() -> None:
    profile = make_profile(
        profile_id=42,
        name="Metadata profile",
        target_customer_types=["factories"],
        countries=["UAE"],
        cities=["Dubai"],
        languages=["en"],
        query_templates=["{target_customer_type} {language} {city} {country}"],
        result_limit=7,
    )

    query = generate(profile).queries[0]

    assert query.profile_id == 42
    assert query.profile_name == "Metadata profile"
    assert query.language == "en"
    assert query.country == "UAE"
    assert query.city == "Dubai"
    assert query.source_template == "{target_customer_type} {language} {city} {country}"
    assert query.limit == 7


def test_preview_invariants() -> None:
    preview = generate(make_profile())

    assert isinstance(preview, SearchQueryPreview)
    assert preview.query_count == len(preview.queries)
    assert preview.estimated_provider_requests == len(preview.queries)


def test_preview_never_calls_database_or_provider() -> None:
    profile = make_profile(target_customer_types=["small businesses"])

    preview = generate(profile)

    assert preview.queries[0].text == "small businesses"


def test_deterministic_output_across_repeated_calls() -> None:
    profile = make_profile(
        target_customer_types=["offices", "hotels"],
        countries=["Australia"],
        cities=["Sydney", "Melbourne"],
        result_limit=7,
        total_result_ceiling=18,
    )

    first = generate(profile)
    second = generate(profile)

    assert first == second
    assert [query.limit for query in first.queries] == [7, 7, 4]


def test_input_profile_lists_are_not_mutated() -> None:
    profile = make_profile(
        target_customer_types=["Hotels"],
        negative_keywords=["jobs", "jobs"],
        query_templates=[],
        result_limit=10,
        total_result_ceiling=5,
    )
    original = profile.model_dump()

    generate(profile)

    assert profile.model_dump() == original


def test_template_without_audience_placeholder_is_allowed() -> None:
    profile = make_profile(
        product_or_service="commercial cleaning",
        query_templates=["{product_or_service} {country}"],
        countries=["Australia"],
    )

    assert generate(profile).queries[0].text == "commercial cleaning Australia"

import pytest
from pydantic import ValidationError

from app.modules.search_profile import SearchProfileCreate, SearchProfileUpdate


def make_profile_data() -> dict[str, object]:
    return {
        "project_id": 1,
        "name": "Universal Search",
        "product_or_service": "lead generation",
        "target_customer_types": ["small businesses"],
    }


def test_valid_profile_with_customer_types() -> None:
    profile = SearchProfileCreate.model_validate(
        {
            "project_id": 1,
            "name": "Furniture buyers",
            "product_or_service": "handcrafted furniture",
            "target_customer_types": ["interior designers"],
        }
    )

    assert profile.target_customer_types == ["interior designers"]


def test_valid_profile_with_industries_only() -> None:
    profile = SearchProfileCreate.model_validate(
        {
            "project_id": 1,
            "name": "Industrial equipment",
            "product_or_service": "industrial equipment",
            "target_industries": ["manufacturing"],
        }
    )

    assert profile.target_industries == ["manufacturing"]


def test_valid_profile_with_positive_keywords_only() -> None:
    profile = SearchProfileCreate.model_validate(
        {
            "project_id": 1,
            "name": "Recruitment services",
            "product_or_service": "recruitment services",
            "positive_keywords": ["technology startups"],
        }
    )

    assert profile.positive_keywords == ["technology startups"]


def test_valid_global_profile_with_no_country_or_city() -> None:
    profile = SearchProfileCreate.model_validate(make_profile_data())

    assert profile.countries == []
    assert profile.cities == []


def test_blank_name_rejected() -> None:
    data = make_profile_data()
    data["name"] = "   "

    with pytest.raises(ValidationError, match="Search profile name is required"):
        SearchProfileCreate.model_validate(data)


def test_blank_product_or_service_rejected() -> None:
    data = make_profile_data()
    data["product_or_service"] = "\t"

    with pytest.raises(ValidationError, match="Product or service is required"):
        SearchProfileCreate.model_validate(data)


def test_no_targeting_dimensions_rejected() -> None:
    data = make_profile_data()
    data["target_customer_types"] = []

    with pytest.raises(ValidationError, match="At least one targeting dimension"):
        SearchProfileCreate.model_validate(data)


def test_list_items_are_stripped() -> None:
    data = make_profile_data()
    data["target_customer_types"] = ["  hotels  "]
    data["countries"] = ["  Germany  "]

    profile = SearchProfileCreate.model_validate(data)

    assert profile.target_customer_types == ["hotels"]
    assert profile.countries == ["Germany"]


def test_blank_list_items_are_removed() -> None:
    data = make_profile_data()
    data["target_customer_types"] = ["hotels", " ", "\t", "retailers"]

    profile = SearchProfileCreate.model_validate(data)

    assert profile.target_customer_types == ["hotels", "retailers"]


def test_duplicate_list_values_are_removed_case_insensitively() -> None:
    data = make_profile_data()
    data["target_customer_types"] = ["Hotels", "hotels", "HOTELS", "retailers"]

    profile = SearchProfileCreate.model_validate(data)

    assert profile.target_customer_types == ["Hotels", "retailers"]


def test_list_order_is_preserved() -> None:
    data = make_profile_data()
    data["target_customer_types"] = ["offices", "hotels", "malls"]

    profile = SearchProfileCreate.model_validate(data)

    assert profile.target_customer_types == ["offices", "hotels", "malls"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("result_limit", 0),
        ("result_limit", 101),
        ("max_queries_per_run", 0),
        ("max_queries_per_run", 101),
        ("total_result_ceiling", 0),
        ("total_result_ceiling", 1001),
    ],
)
def test_numeric_limits_enforced(field: str, value: int) -> None:
    data = make_profile_data()
    data[field] = value

    with pytest.raises(ValidationError):
        SearchProfileCreate.model_validate(data)


def test_mutable_defaults_are_independent() -> None:
    first = SearchProfileCreate.model_validate(make_profile_data())
    second = SearchProfileCreate.model_validate(make_profile_data())

    first.target_customer_types.append("accounting firms")

    assert first.target_customer_types == ["small businesses", "accounting firms"]
    assert second.target_customer_types == ["small businesses"]


def test_query_templates_are_normalized_without_requiring_defaults() -> None:
    data = make_profile_data()
    data["query_templates"] = [
        "  {target_customer_type} {country}  ",
        "",
        "{TARGET_CUSTOMER_TYPE} {COUNTRY}",
    ]

    profile = SearchProfileCreate.model_validate(data)

    assert profile.query_templates == ["{target_customer_type} {country}"]


def test_update_schema_normalizes_supplied_values_without_targeting_requirement() -> None:
    update = SearchProfileUpdate.model_validate(
        {
            "name": "  Accounting SaaS  ",
            "target_customer_types": [" Firms ", "firms", ""],
        }
    )

    assert update.name == "Accounting SaaS"
    assert update.target_customer_types == ["Firms"]


def test_unrelated_business_examples_are_valid() -> None:
    examples = [
        {
            "name": "Furniture",
            "product_or_service": "handcrafted furniture",
            "target_customer_types": ["interior designers"],
        },
        {
            "name": "Accounting SaaS",
            "product_or_service": "accounting SaaS",
            "target_customer_types": ["small businesses"],
        },
        {
            "name": "Industrial equipment",
            "product_or_service": "industrial equipment",
            "target_industries": ["manufacturing"],
        },
        {
            "name": "Recruitment",
            "product_or_service": "recruitment services",
            "positive_keywords": ["technology startups"],
        },
        {
            "name": "Commercial cleaning",
            "product_or_service": "commercial cleaning",
            "target_customer_types": ["property managers"],
        },
    ]

    profiles = [
        SearchProfileCreate.model_validate({"project_id": index, **example})
        for index, example in enumerate(examples, start=1)
    ]

    assert [profile.name for profile in profiles] == [
        "Furniture",
        "Accounting SaaS",
        "Industrial equipment",
        "Recruitment",
        "Commercial cleaning",
    ]

import socket
from typing import Any, cast

import pytest

from app.core.country_targets import (
    CountryTarget,
    get_country_target,
    normalize_iso_country_code,
    normalize_iso_country_codes,
)


def test_normalize_lowercase_input_to_canonical_uppercase() -> None:
    assert normalize_iso_country_code("us") == "US"


def test_normalize_whitespace_trim_collapse() -> None:
    assert normalize_iso_country_code("  gb ") == "GB"


def test_us_lookup() -> None:
    target = get_country_target("us")
    assert target.iso_alpha2 == "US"
    assert target.display_name == "United States"
    assert target.serpapi_gl == "us"


def test_de_lookup() -> None:
    target = get_country_target("de")
    assert target.iso_alpha2 == "DE"
    assert target.display_name == "Germany"
    assert target.serpapi_gl == "de"


def test_id_lookup() -> None:
    target = get_country_target("ID")
    assert target.iso_alpha2 == "ID"
    assert target.display_name == "Indonesia"
    assert target.serpapi_gl == "id"


def test_gb_lookup() -> None:
    target = get_country_target("gb")
    assert target.iso_alpha2 == "GB"
    assert target.display_name == "United Kingdom"
    assert target.serpapi_gl == "uk"


def test_gb_mapper_is_explicitly_overridden_to_uk() -> None:
    assert get_country_target("GB").serpapi_gl == "uk"


def test_uk_is_not_accepted_as_canonical_iso_input() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_code("UK")


def test_zz_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_code("ZZ")


def test_historic_code_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_code("SU")


def test_blank_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_code("   ")


def test_non_string_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_code(123)


def test_plain_string_rejected_as_collection() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_codes("US", max_items=20)


def test_duplicate_collection_codes_are_removed() -> None:
    assert normalize_iso_country_codes(["us", "DE", "DE", "US"], max_items=20) == (
        "DE",
        "US",
    )


def test_deterministic_collection_sorting() -> None:
    assert normalize_iso_country_codes(["US", "id", "DE", "gb"], max_items=20) == (
        "DE",
        "GB",
        "ID",
        "US",
    )


def test_duplicate_overflow_is_accepted_when_normalized() -> None:
    assert normalize_iso_country_codes(["US"] * 21, max_items=20) == ("US",)


def test_duplicate_normalized_codes_can_exceed_unique_limit() -> None:
    assert normalize_iso_country_codes(
        ["us"] * 10 + [" US "] * 10 + ["US"] * 10,
        max_items=20,
    ) == ("US",)


def test_empty_explicit_collection_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_codes([], max_items=20)


def test_empty_explicit_collection_allowed_if_optional_mode() -> None:
    assert normalize_iso_country_codes([], max_items=20, allow_empty=True) == ()


def test_max_items_accepts_limit() -> None:
    normalize_iso_country_codes(
        (
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
        ),
        max_items=20,
    )


def test_too_many_codes_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_codes(
            (
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
                "BE",
            ),
            max_items=20,
        )


def test_twenty_unique_codes_are_accepted() -> None:
    normalized = normalize_iso_country_codes(
        (
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
        ),
        max_items=20,
    )
    assert normalized == (
        "AU",
        "BR",
        "CA",
        "CN",
        "DE",
        "DK",
        "ES",
        "FI",
        "FR",
        "GB",
        "ID",
        "IN",
        "IT",
        "JP",
        "MX",
        "NL",
        "NO",
        "RU",
        "SE",
        "US",
    )
    assert len(normalized) == 20


def test_twenty_one_unique_codes_are_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_codes(
            (
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
                "BE",
            ),
            max_items=20,
        )


def test_duplicate_heavy_inputs_with_twenty_unique_codes_are_accepted() -> None:
    assert normalize_iso_country_codes(
        (
            "us",
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
        ),
        max_items=20,
    ) == (
        "AU",
        "BR",
        "CA",
        "CN",
        "DE",
        "DK",
        "ES",
        "FI",
        "FR",
        "GB",
        "ID",
        "IN",
        "IT",
        "JP",
        "MX",
        "NL",
        "NO",
        "RU",
        "SE",
        "US",
    )


def test_duplicate_heavy_inputs_with_twenty_one_unique_codes_are_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_codes(
            (
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
                "BE",
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
            ),
            max_items=20,
        )


def test_duplicate_overflow_does_not_hide_invalid_values() -> None:
    with pytest.raises(ValueError):
        normalize_iso_country_codes(["US"] * 25 + ["ZZ"], max_items=20)


def test_country_target_is_frozen() -> None:
    target = get_country_target("US")
    with pytest.raises(AttributeError):
        setattr(cast(Any, target), "iso_alpha2", "DE")  # noqa: B010

    with pytest.raises(AttributeError):
        setattr(cast(Any, target), "serpapi_gl", "xx")  # noqa: B010


def test_no_network_socket_calls_during_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"Unexpected network use: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", forbidden)

    assert normalize_iso_country_code("GB") == "GB"
    assert get_country_target("DE") == CountryTarget(
        iso_alpha2="DE",
        display_name="Germany",
        serpapi_gl="de",
    )

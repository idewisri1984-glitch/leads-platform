from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, cast


class _CountryRecord(Protocol):
    alpha_2: str
    name: str


class _CountriesCollection(Protocol):
    def get(self, **kwargs: Any) -> _CountryRecord | None: ...


class _PyCountry(Protocol):
    countries: _CountriesCollection


_ISO_LETTERS_PATTERN = re.compile(r"^[A-Z]{2}$", flags=re.ASCII)


@dataclass(frozen=True)
class CountryTarget:
    iso_alpha2: str
    display_name: str
    serpapi_gl: str


def normalize_iso_country_code(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Country code must be a string.")

    trimmed = _normalize_text(value)
    normalized = trimmed.upper()

    if normalized == "UK":
        raise ValueError("UK is not a valid canonical ISO alpha-2 code.")

    if "<" in normalized or ">" in normalized:
        raise ValueError("Country code must not include markup.")

    if not _ISO_LETTERS_PATTERN.fullmatch(normalized):
        raise ValueError("Country code must contain exactly two ASCII letters.")

    target = _get_country_target(normalized)
    return target.iso_alpha2


def get_country_target(value: object) -> CountryTarget:
    iso_alpha2 = normalize_iso_country_code(value)
    return _get_country_target(iso_alpha2)


def normalize_iso_country_codes(
    values: object, *, max_items: int, allow_empty: bool = False
) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("Country codes must be a collection.")

    if not isinstance(values, Iterable):
        raise ValueError("Country codes must be a collection.")

    if values is None:
        if allow_empty:
            return ()
        raise ValueError("Country codes must be a non-empty collection.")

    normalized: list[str] = []
    for item in cast(Iterable[object], values):
        normalized.append(normalize_iso_country_code(item))

    if not allow_empty and not normalized:
        raise ValueError("At least one country code is required.")

    if len(normalized) > max_items:
        raise ValueError("Too many country codes.")

    return tuple(sorted({code for code in normalized if code is not None}))


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _get_country_target(iso_alpha2: str) -> CountryTarget:
    module = _load_pycountry()
    country = module.countries.get(alpha_2=iso_alpha2)
    if country is None:
        raise ValueError("Unknown ISO country code.")

    display_name = _normalize_text(country.name)
    if not display_name:
        raise ValueError("Country name must not be empty.")

    if len(display_name) > 255:
        raise ValueError("Country display name is too long.")

    serpapi_gl = iso_alpha2.casefold()
    if iso_alpha2 == "GB":
        serpapi_gl = "uk"

    return CountryTarget(
        iso_alpha2=iso_alpha2,
        display_name=display_name,
        serpapi_gl=serpapi_gl,
    )


def _load_pycountry() -> _PyCountry:
    return cast(_PyCountry, import_module("pycountry"))

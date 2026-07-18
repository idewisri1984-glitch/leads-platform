import re

import pytest
from pydantic import ValidationError

from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoverySourceMode,
)


def snapshot(**changes: object) -> CompanyDiscoveryRequestSnapshot:
    values: dict[str, object] = {
        "source_mode": CompanyDiscoverySourceMode.AD_HOC,
        "country_codes": [" us ", "GB", "US"],
        "query_count": 2,
        "result_limit": 10,
        "total_result_ceiling": 20,
    }
    values.update(changes)
    return CompanyDiscoveryRequestSnapshot(**values)


def test_snapshot_normalizes_deduplicates_and_sorts_countries() -> None:
    assert snapshot().country_codes == ("GB", "US")


@pytest.mark.parametrize("code", ["U", "USA", "1A", "ÜS", "U-"])
def test_snapshot_rejects_malformed_country(code: str) -> None:
    with pytest.raises(ValidationError):
        snapshot(country_codes=[code])


def test_snapshot_enforces_country_limit_and_extra_fields() -> None:
    countries = [
        f"{chr(65 + first)}{chr(65 + second)}" for first in range(5) for second in range(5)
    ]
    with pytest.raises(ValidationError):
        snapshot(country_codes=countries)
    with pytest.raises(ValidationError):
        snapshot(api_key="secret")


def test_snapshot_source_mode_profile_consistency() -> None:
    with pytest.raises(ValidationError):
        snapshot(source_mode="SEARCH_PROFILE")
    with pytest.raises(ValidationError):
        snapshot(search_profile_id=1)
    profile = snapshot(source_mode="SEARCH_PROFILE", search_profile_id=1)
    assert profile.search_profile_id == 1


def test_snapshot_serialization_and_fingerprint_are_deterministic() -> None:
    first = snapshot(country_codes=["US", "GB", "US"])
    second = snapshot(country_codes=["gb", "us"])
    assert first.canonical_json() == second.canonical_json()
    assert first.fingerprint() == second.fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", first.fingerprint())
    assert first.fingerprint() != snapshot(result_limit=11).fingerprint()


def test_snapshot_has_no_sensitive_or_unbounded_fields() -> None:
    fields = set(CompanyDiscoveryRequestSnapshot.model_fields)
    assert fields == {
        "source_mode",
        "search_profile_id",
        "country_codes",
        "query_count",
        "result_limit",
        "total_result_ceiling",
    }

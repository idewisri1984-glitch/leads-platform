import re

import pytest
from pydantic import ValidationError

from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoverySourceMode,
)
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidatePreview,
    CompanyDiscoveryStagingRunResult,
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


def test_snapshot_allows_duplicate_heavy_inputs_with_unique_limit() -> None:
    assert snapshot(
        country_codes=["US"] * 30 + ["gb", " us ", "US", "DE", " de ", "DE"] * 2,
        query_count=2,
    ).country_codes == ("DE", "GB", "US")


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


def test_snapshot_treats_21_repeated_codes_as_single_fingerprint_code() -> None:
    first = snapshot(country_codes=["US"] * 21, query_count=1)
    second = snapshot(country_codes=["us", " US ", "US"], query_count=1)
    third = snapshot(country_codes=["US"], query_count=1)

    assert first.country_codes == ("US",)
    assert second.country_codes == ("US",)
    assert third.country_codes == ("US",)

    assert first.fingerprint() == second.fingerprint() == third.fingerprint()


def test_snapshot_rejects_too_many_unique_countries() -> None:
    with pytest.raises(ValidationError):
        snapshot(
            country_codes=[
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
            ],
            query_count=1,
        )


def test_invalid_country_code_is_rejected_before_fingerprint() -> None:
    with pytest.raises(ValidationError):
        snapshot(country_codes=["US", "ZZ", "DE"], query_count=1)


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


def run_result(**values: object) -> CompanyDiscoveryStagingRunResult:
    base = {
        "project_id": 1,
        "search_profile_id": 2,
        "profile_name": "Demo profile",
        "provider": "serpapi",
        "dry_run": True,
        "status": CompanyDiscoveryRunStatus.NOT_FOUND,
        "request_fingerprint": "a" * 64,
        "query_count": 1,
        "executed_queries": 1,
        "successful_queries": 1,
        "provider_result_count": 0,
        "provider_error_count": 0,
        "existing_adapter_error_count": 0,
        "rejected_candidate_count": 0,
        "duplicate_candidate_count": 0,
        "unique_candidate_count": 0,
        "candidate_upserts": 0,
        "candidates_created": 0,
        "candidates_updated": 0,
        "candidates_protected": 0,
        "run_persisted": False,
        "run_id": None,
        "candidates": [],
    }
    base.update(values)
    return CompanyDiscoveryStagingRunResult(**base)


def test_staging_run_result_dry_run_allows_one_candidate_preview() -> None:
    result = run_result(
        dry_run=True,
        status=CompanyDiscoveryRunStatus.SUCCEEDED,
        error_code=None,
        unique_candidate_count=1,
        candidates=[
            CompanyDiscoveryStagingCandidatePreview(
                name="Acme",
                website="https://example.com",
                website_identity="example.com",
                country_code="DE",
                best_position=1,
                identity_key="name_country:acme|DE",
            )
        ],
    )

    assert len(result.candidates) == 1
    assert result.unique_candidate_count == 1
    assert result.candidate_upserts == 0


def test_staging_run_result_dry_run_allows_multiple_candidate_previews() -> None:
    result = run_result(
        dry_run=True,
        status=CompanyDiscoveryRunStatus.SUCCEEDED,
        error_code=None,
        unique_candidate_count=2,
        candidates=[
            CompanyDiscoveryStagingCandidatePreview(
                name="Acme",
                website="https://example.com",
                website_identity="example.com",
                country_code="DE",
                best_position=1,
                identity_key="name_country:acme|DE",
            ),
            CompanyDiscoveryStagingCandidatePreview(
                name="Beta",
                website="https://beta.example.com",
                website_identity="beta.example.com",
                country_code="FR",
                best_position=2,
                identity_key="name_country:beta|FR",
            ),
        ],
    )

    assert len(result.candidates) == 2
    assert result.unique_candidate_count == 2
    assert result.candidates_created == 0
    assert result.candidates_updated == 0
    assert result.candidates_protected == 0


def test_staging_run_result_dry_run_cannot_claim_upserts() -> None:
    with pytest.raises(ValidationError):
        run_result(
            dry_run=True,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            error_code=None,
            unique_candidate_count=1,
            candidate_upserts=1,
            candidates=[
                CompanyDiscoveryStagingCandidatePreview(
                    name="Acme",
                    website="https://example.com",
                    website_identity="example.com",
                    country_code="DE",
                    best_position=1,
                    identity_key="name_country:acme|DE",
                )
            ],
        )


@pytest.mark.parametrize(
    "field",
    [
        "candidates_created",
        "candidates_updated",
        "candidates_protected",
    ],
)
def test_staging_run_result_dry_run_cannot_claim_persistence_counters(field: str) -> None:
    field_name = field
    values = {
        "dry_run": True,
        "status": CompanyDiscoveryRunStatus.SUCCEEDED,
        "error_code": None,
        "unique_candidate_count": 1,
        "candidate_upserts": 0,
        "candidates": [
            CompanyDiscoveryStagingCandidatePreview(
                name="Acme",
                website="https://example.com",
                website_identity="example.com",
                country_code="DE",
                best_position=1,
                identity_key="name_country:acme|DE",
            )
        ],
    }
    values[field_name] = 1
    with pytest.raises(ValidationError):
        run_result(**values)


def test_staging_run_result_unique_candidate_count_must_match_preview_len() -> None:
    with pytest.raises(ValidationError):
        run_result(
            dry_run=True,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            unique_candidate_count=2,
            candidates=[
                CompanyDiscoveryStagingCandidatePreview(
                    name="Acme",
                    website="https://example.com",
                    website_identity="example.com",
                    country_code="DE",
                    best_position=1,
                    identity_key="name_country:acme|DE",
                )
            ],
        )


def test_staging_run_result_persisted_succeeded_requires_matching_upserts() -> None:
    result = run_result(
        dry_run=False,
        run_persisted=True,
        run_id=1,
        status=CompanyDiscoveryRunStatus.SUCCEEDED,
        error_code=None,
        unique_candidate_count=1,
        candidate_upserts=1,
        candidates_created=1,
        candidates=[
            CompanyDiscoveryStagingCandidatePreview(
                name="Acme",
                website="https://example.com",
                website_identity="example.com",
                country_code="DE",
                best_position=1,
                identity_key="name_country:acme|DE",
            )
        ],
    )
    assert result.run_id == 1
    assert result.candidate_upserts == 1
    assert result.unique_candidate_count == 1


def test_staging_run_result_persisted_partial_allows_matching_upserts() -> None:
    result = run_result(
        dry_run=False,
        run_persisted=True,
        run_id=99,
        status=CompanyDiscoveryRunStatus.PARTIAL,
        error_code="candidate_invalid",
        unique_candidate_count=2,
        candidate_upserts=2,
        candidates=[
            CompanyDiscoveryStagingCandidatePreview(
                name="Acme",
                website="https://example.com",
                website_identity="example.com",
                country_code="DE",
                best_position=1,
                identity_key="name_country:acme|DE",
            ),
            CompanyDiscoveryStagingCandidatePreview(
                name="Beta",
                website="https://beta.example.com",
                website_identity="beta.example.com",
                country_code="FR",
                best_position=1,
                identity_key="name_country:beta|FR",
            ),
        ],
    )
    assert result.run_id == 99
    assert result.candidate_upserts == 2
    assert result.unique_candidate_count == 2


@pytest.mark.parametrize(
    "status,unique_count,upsert_count",
    [
        (CompanyDiscoveryRunStatus.SUCCEEDED, 2, 1),
        (CompanyDiscoveryRunStatus.PARTIAL, 2, 1),
    ],
)
def test_staging_run_result_persisted_success_or_partial_must_match_upserts(
    status: CompanyDiscoveryRunStatus, unique_count: int, upsert_count: int
) -> None:
    with pytest.raises(ValidationError):
        run_result(
            dry_run=False,
            run_persisted=True,
            run_id=1,
            status=status,
            error_code=None
            if status == CompanyDiscoveryRunStatus.SUCCEEDED
            else "candidate_invalid",
            unique_candidate_count=unique_count,
            candidate_upserts=upsert_count,
            candidates=[
                CompanyDiscoveryStagingCandidatePreview(
                    name="Acme",
                    website="https://example.com",
                    website_identity="example.com",
                    country_code="DE",
                    best_position=1,
                    identity_key="name_country:acme|DE",
                ),
                CompanyDiscoveryStagingCandidatePreview(
                    name="Beta",
                    website="https://beta.example.com",
                    website_identity="beta.example.com",
                    country_code="FR",
                    best_position=1,
                    identity_key="name_country:beta|FR",
                ),
            ],
        )


def test_staging_run_result_not_found_persisted_with_zero_counts() -> None:
    result = run_result(
        dry_run=False,
        run_persisted=True,
        run_id=1,
        status=CompanyDiscoveryRunStatus.NOT_FOUND,
        error_code=None,
        unique_candidate_count=0,
        candidate_upserts=0,
        candidates=[],
    )
    assert result.status == CompanyDiscoveryRunStatus.NOT_FOUND


def test_staging_run_result_failed_persisted_with_zero_counts() -> None:
    result = run_result(
        dry_run=False,
        run_persisted=True,
        run_id=1,
        status=CompanyDiscoveryRunStatus.FAILED,
        error_code="execution_failed",
        unique_candidate_count=0,
        candidate_upserts=0,
        candidates=[],
    )
    assert result.status == CompanyDiscoveryRunStatus.FAILED


def test_staging_run_result_run_persisted_requires_run_id() -> None:
    with pytest.raises(ValidationError):
        run_result(
            dry_run=False,
            run_persisted=True,
            run_id=None,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            error_code=None,
            unique_candidate_count=0,
            candidate_upserts=0,
        )


def test_staging_run_result_non_persisted_cannot_have_run_id() -> None:
    with pytest.raises(ValidationError):
        run_result(
            dry_run=True,
            status=CompanyDiscoveryRunStatus.NOT_FOUND,
            error_code=None,
            unique_candidate_count=0,
            candidate_upserts=0,
            run_id=1,
        )


def test_staging_run_result_counters_cannot_exceed_upserts() -> None:
    with pytest.raises(ValidationError):
        run_result(
            dry_run=False,
            run_persisted=True,
            run_id=1,
            status=CompanyDiscoveryRunStatus.SUCCEEDED,
            error_code=None,
            unique_candidate_count=1,
            candidate_upserts=1,
            candidates_created=2,
            candidates=[
                CompanyDiscoveryStagingCandidatePreview(
                    name="Acme",
                    website="https://example.com",
                    website_identity="example.com",
                    country_code="DE",
                    best_position=1,
                    identity_key="name_country:acme|DE",
                )
            ],
        )


def test_staging_run_result_protected_updated_overlap_is_allowed() -> None:
    result = run_result(
        dry_run=False,
        run_persisted=True,
        run_id=1,
        status=CompanyDiscoveryRunStatus.SUCCEEDED,
        error_code=None,
        unique_candidate_count=1,
        candidate_upserts=1,
        candidates_updated=1,
        candidates_protected=1,
        candidates=[
            CompanyDiscoveryStagingCandidatePreview(
                name="Acme",
                website="https://example.com",
                website_identity="example.com",
                country_code="DE",
                best_position=1,
                identity_key="name_country:acme|DE",
            )
        ],
    )
    assert result.candidates_updated == 1
    assert result.candidates_protected == 1

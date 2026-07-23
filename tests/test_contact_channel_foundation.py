from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.modules.contact.channel_normalization import (
    normalize_instagram_url,
    normalize_linkedin_url,
)
from app.modules.contact.schemas import ContactCreate
from app.modules.contact_discovery.models import ContactDiscoverySourceType
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
)
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate


def test_named_contact_contract_remains_unchanged() -> None:
    contact = ContactCreate(company_id=1, first_name="  Ada   ", last_name=" Lovelace ")
    assert contact.first_name == "Ada"
    assert contact.last_name == "Lovelace"


@pytest.mark.parametrize(
    "channel",
    [
        {"email": "info@example.com"},
        {"phone": "+1 212 555 0100"},
        {"linkedin_url": "https://linkedin.com/company/example"},
        {"instagram_url": "https://instagram.com/example"},
    ],
)
def test_generic_contact_accepts_one_usable_channel(channel: dict[str, str]) -> None:
    contact = ContactCreate(company_id=1, first_name="   ", **channel)
    assert contact.first_name is None


def test_fully_empty_anonymous_contact_is_rejected() -> None:
    with pytest.raises(ValidationError, match="usable contact channel"):
        ContactCreate(company_id=1, first_name=" ")


def test_social_urls_are_canonicalized_without_network_access() -> None:
    assert (
        normalize_linkedin_url(
            "HTTP://WWW.LinkedIn.COM/company/example/?trk=public&view=compact#about"
        )
        == "https://www.linkedin.com/company/example?view=compact"
    )
    assert (
        normalize_instagram_url("https://INSTAGRAM.com/example/?utm_source=test#profile")
        == "https://www.instagram.com/example"
    )


@pytest.mark.parametrize(
    ("normalizer", "value"),
    [
        (normalize_linkedin_url, "https://user:pass@linkedin.com/in/person"),
        (normalize_linkedin_url, "https://example.com/company/example"),
        (normalize_linkedin_url, "linkedin.com/company/example"),
        (normalize_instagram_url, "https://example.com/example"),
        (normalize_instagram_url, "https://instagram.com/p/post"),
        (normalize_instagram_url, "ftp://instagram.com/example"),
    ],
)
def test_invalid_social_urls_are_rejected(
    normalizer: Callable[[str | None], str | None], value: str
) -> None:
    with pytest.raises(ValueError):
        normalizer(value)


def test_staging_candidates_accept_generic_and_social_only_channels() -> None:
    generic = ContactDiscoveryCandidateCreate(
        company_id=1,
        email="info@example.com",
        source_type=ContactDiscoverySourceType.CONTACT_PAGE,
    )
    linkedin = ContactDiscoveryCandidateCreate(
        company_id=1,
        linkedin_url="https://linkedin.com/company/example",
        source_type=ContactDiscoverySourceType.OTHER_PUBLIC_PAGE,
    )
    instagram = ContactDiscoveryCandidateCreate(
        company_id=1,
        instagram_url="https://instagram.com/example",
        source_type=ContactDiscoverySourceType.OTHER_PUBLIC_PAGE,
    )
    assert generic.name is None
    assert linkedin.linkedin_url == "https://www.linkedin.com/company/example"
    assert instagram.instagram_url == "https://www.instagram.com/example"


def test_staging_candidate_rejects_anonymous_record_without_channel() -> None:
    with pytest.raises(ValidationError, match="usable channel"):
        ContactDiscoveryCandidateCreate(
            company_id=1,
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        )


def test_existing_dedupe_keys_remain_byte_for_byte_unchanged() -> None:
    assert (
        build_contact_candidate_deduplication_key(
            email=" ADA@Example.COM ",
            name="Ada",
            title="Director",
            source_url="https://example.com/team",
            phone="+1 212 555 0100",
            linkedin_url="https://linkedin.com/in/ada",
        )
        == "email:ada@example.com"
    )
    assert (
        build_contact_candidate_deduplication_key(
            email=None,
            name=" Ada  Lovelace ",
            title="Design DIRECTOR",
            source_url="https://example.com/team?one=1#staff",
            phone="+1 212 555 0100",
        )
        == "person:ada lovelace|design director|example.com/team"
    )


def test_new_channel_only_dedupe_keys_are_stable() -> None:
    assert (
        build_contact_candidate_deduplication_key(
            email=None,
            name=None,
            title=None,
            source_url=None,
            phone="+1 (212) 555-0100",
        )
        == "phone:+12125550100"
    )
    assert (
        build_contact_candidate_deduplication_key(
            email=None,
            name=None,
            title=None,
            source_url=None,
            linkedin_url="https://linkedin.com/company/example?trk=public",
        )
        == "linkedin:https://www.linkedin.com/company/example"
    )
    assert (
        build_contact_candidate_deduplication_key(
            email=None,
            name=None,
            title=None,
            source_url=None,
            instagram_url="https://instagram.com/example?utm_source=test",
        )
        == "instagram:https://www.instagram.com/example"
    )

from pathlib import Path

import pytest

from app.modules.contact_discovery.models import ContactDiscoverySourceType
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.website_contact_parser import (
    MAX_HTML_LENGTH,
    parse_contact_discovery_candidates_from_html,
)

SOURCE_URL = "https://example.com/team?ref=nav#people"


def parse(html: str) -> list[ContactDiscoveryCandidateCreate]:
    return parse_contact_discovery_candidates_from_html(
        company_id=7,
        html=html,
        source_url=SOURCE_URL,
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
    )


def test_extracts_person_card_with_name_and_title() -> None:
    candidates = parse(
        '<section id="team"><div class="person"><h3>Ada Lovelace</h3>'
        '<p class="role">Design Director</p></div></section>'
    )
    assert [(item.name, item.title) for item in candidates] == [("Ada Lovelace", "Design Director")]
    assert candidates[0].confidence == 65


def test_non_leadership_role_is_allowed_but_does_not_get_keyword_bonus() -> None:
    candidates = parse(
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Architect</p></div>'
    )
    assert candidates[0].title == "Architect"
    assert candidates[0].confidence == 35


def test_extracts_mailto_email_and_normalizes_it() -> None:
    candidates = parse(
        '<div class="person"><h3>Ada Lovelace</h3><p class="title">Founder</p>'
        '<a href="mailto: ADA@Example.COM ?subject=Hi">Email</a></div>'
    )
    assert candidates[0].email == "ada@example.com"


def test_extracts_visible_email() -> None:
    candidates = parse(
        '<div class="staff-member"><h3>Ada Lovelace</h3><span class="role">Principal</span>'
        "ada@example.com</div>"
    )
    assert candidates[0].email == "ada@example.com"


@pytest.mark.parametrize(
    ("phone_html", "expected"),
    [
        ('<a href="tel:+1-212-555-0100">Call</a>', "+1-212-555-0100"),
        ("<span>+1 (212) 555-0100</span>", "+1 (212) 555-0100"),
    ],
)
def test_extracts_phone_inside_person_card(phone_html: str, expected: str) -> None:
    candidates = parse(
        f'<div class="person"><h3>Ada Lovelace</h3><p class="role">Director</p>{phone_html}</div>'
    )
    assert candidates[0].phone == expected


def test_extracts_multiple_repeated_team_cards() -> None:
    candidates = parse(
        '<section class="team"><div class="person"><h3>Ada Lovelace</h3>'
        '<p class="role">Founder</p></div><div class="person"><h3>Grace Hopper</h3>'
        '<p class="role">Managing Director</p></div></section>'
    )
    assert [item.name for item in candidates] == ["Ada Lovelace", "Grace Hopper"]


def test_extracts_heading_subheading_in_team_section() -> None:
    candidates = parse(
        '<section aria-label="team"><h2>Our Team</h2><h3>Ada Lovelace</h3>'
        "<p>Design Director</p></section>"
    )
    assert [(item.name, item.title) for item in candidates] == [("Ada Lovelace", "Design Director")]


def test_extracts_table_row() -> None:
    candidates = parse(
        "<table><tr><th>Ada Lovelace</th><td>Procurement Director</td>"
        "<td>ada@example.com</td></tr></table>"
    )
    assert candidates[0].name == "Ada Lovelace"
    assert candidates[0].title == "Procurement Director"
    assert candidates[0].email == "ada@example.com"


def test_extracts_definition_list() -> None:
    candidates = parse(
        '<dl class="staff"><dt><strong>Ada Lovelace</strong></dt>'
        '<dd class="position">Hospitality Director</dd></dl>'
    )
    assert candidates[0].name == "Ada Lovelace"


def test_generic_email_requires_named_person() -> None:
    assert parse('<footer><a href="mailto:info@example.com">info@example.com</a></footer>') == []
    candidates = parse(
        '<div class="person"><h3>Ada Lovelace</h3><p class="title">Founder</p>'
        "<span>info@example.com</span></div>"
    )
    assert candidates[0].email == "info@example.com"
    assert candidates[0].confidence < 100


def test_does_not_invent_email_or_assign_footer_phone() -> None:
    candidates = parse(
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Director</p></div>'
        "<footer>Company phone +1 212 555 0199</footer>"
    )
    assert candidates[0].email is None
    assert candidates[0].phone is None


@pytest.mark.parametrize(
    "title",
    [
        "Founder",
        "Principal",
        "Managing Partner",
        "CEO",
        "President",
        "Design Director",
        "Hospitality Director",
        "Procurement Director",
        "Purchasing Director",
    ],
)
def test_recognizes_leadership_titles(title: str) -> None:
    candidates = parse(
        f'<div class="person"><h3>Ada Lovelace</h3><p class="role">{title}</p></div>'
    )
    assert candidates[0].title == title


def test_leadership_keyword_and_section_context_increase_confidence() -> None:
    basic = parse('<div class="person"><h3>Ada Lovelace</h3><p class="role">Director</p></div>')[0]
    contextual = parse(
        '<section id="leadership"><div class="person"><h3>Ada Lovelace</h3>'
        '<p class="role">Director</p></div></section>'
    )[0]
    assert basic.confidence == 45
    assert contextual.confidence == 65


def test_skips_article_author_testimonial_and_customer_names() -> None:
    assert (
        parse(
            '<article><div class="author"><h3>Article Writer</h3><p>Director</p></div></article>'
            '<div class="testimonial-person"><h3>Happy Customer</h3><p>Founder</p></div>'
        )
        == []
    )


def test_parses_schema_org_person_json_ld_without_executing_script() -> None:
    candidates = parse(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Person","name":"Ada Lovelace",'
        '"jobTitle":"Founder","email":"ADA@example.com","telephone":"+1 212 555 0100"}'
        "</script><script>throw new Error('must not run')</script>"
    )
    assert len(candidates) == 1
    assert candidates[0].name == "Ada Lovelace"
    assert candidates[0].email == "ada@example.com"
    assert candidates[0].confidence == 100


def test_invalid_json_ld_is_ignored_safely() -> None:
    assert parse('<script type="application/ld+json">{not-json</script>') == []


def test_duplicate_email_candidates_are_deduplicated_and_merged() -> None:
    candidates = parse(
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p>'
        '<a href="mailto:ada@example.com">Email</a></div>'
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p>'
        '<span>ADA@example.com</span><a href="tel:+12125550100">Call</a></div>'
    )
    assert len(candidates) == 1
    assert candidates[0].phone == "+12125550100"


def test_duplicate_name_title_source_candidates_are_deduplicated() -> None:
    html = '<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>'
    assert len(parse(html + html)) == 1


def test_same_name_with_different_titles_remains_separate_without_email() -> None:
    candidates = parse(
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>'
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Design Director</p></div>'
    )
    assert len(candidates) == 2


def test_query_and_fragment_do_not_change_existing_deduplication_rules() -> None:
    first = parse_contact_discovery_candidates_from_html(
        company_id=7,
        html='<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>',
        source_url="https://example.com/team?first=1#one",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
    )[0]
    second = parse_contact_discovery_candidates_from_html(
        company_id=7,
        html='<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>',
        source_url="https://example.com/team?second=2#two",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
    )[0]
    from app.modules.contact_discovery.normalization import (
        build_contact_candidate_deduplication_key,
    )

    assert build_contact_candidate_deduplication_key(
        email=first.email, name=first.name, title=first.title, source_url=first.source_url
    ) == build_contact_candidate_deduplication_key(
        email=second.email, name=second.name, title=second.title, source_url=second.source_url
    )


def test_oversized_html_is_capped_and_late_content_is_not_processed() -> None:
    html = " " * MAX_HTML_LENGTH + (
        '<div class="person"><h3>Late Person</h3><p class="role">Founder</p></div>'
    )
    assert parse(html) == []


def test_script_style_noscript_and_raw_payload_are_ignored() -> None:
    marker = "SECRET_RAW_MARKER"
    candidates = parse(
        f"<script>{marker} ada@example.com</script><style>{marker}</style>"
        f"<noscript>{marker}</noscript>"
    )
    assert candidates == []
    assert marker not in repr(candidates)
    assert "Traceback" not in repr(candidates)


def test_parser_has_no_network_provider_automation_contact_or_cli_imports() -> None:
    source = (
        Path("app/modules/contact_discovery/website_contact_parser.py")
        .read_text(encoding="utf-8")
        .casefold()
    )
    for forbidden in (
        "httpx",
        "requests",
        "socket",
        "serpapi",
        "selenium",
        "playwright",
        "instagram",
        "linkedin",
        "send_message",
        "contactrepository",
        "from app.modules.contact.models",
        "typer",
        "session",
        "provider",
    ):
        assert forbidden not in source


def test_parser_returns_schemas_only_and_does_not_write_database() -> None:
    candidates = parse('<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>')
    assert candidates
    assert all(type(item) is ContactDiscoveryCandidateCreate for item in candidates)


def test_invalid_company_or_source_is_rejected_without_network() -> None:
    with pytest.raises(ValueError, match="Company ID"):
        parse_contact_discovery_candidates_from_html(
            company_id=0,
            html="",
            source_url=SOURCE_URL,
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        )
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        parse_contact_discovery_candidates_from_html(
            company_id=1,
            html="",
            source_url="file:///secret",
            source_type=ContactDiscoverySourceType.TEAM_PAGE,
        )

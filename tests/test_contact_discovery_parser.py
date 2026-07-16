from pathlib import Path

import pytest

from app.modules.contact_discovery import website_contact_parser
from app.modules.contact_discovery.models import ContactDiscoverySourceType
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.website_contact_parser import (
    MAX_DOM_NODES,
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


def test_unrelated_company_and_product_tables_are_skipped() -> None:
    assert parse("<table><tr><td>Acme Corporation</td><td>Annual Revenue</td></tr></table>") == []
    assert parse("<table><tr><td>Premium Widget</td><td>Stainless Steel</td></tr></table>") == []


def test_unrelated_definition_list_is_skipped() -> None:
    assert parse("<dl><dt>Annual Revenue</dt><dd>Ten Million Dollars</dd></dl>") == []


def test_team_context_table_still_extracts_candidate() -> None:
    candidates = parse(
        '<section id="team"><table><tr><td>Ada Lovelace</td>'
        "<td>Architect</td></tr></table></section>"
    )
    assert [(item.name, item.title) for item in candidates] == [("Ada Lovelace", "Architect")]


def test_table_with_name_and_leadership_title_extracts_without_context() -> None:
    candidates = parse("<table><tr><td>Ada Lovelace</td><td>Founder</td></tr></table>")
    assert [(item.name, item.title) for item in candidates] == [("Ada Lovelace", "Founder")]


def test_table_with_named_person_and_email_extracts_without_context() -> None:
    candidates = parse("<table><tr><td>Ada Lovelace</td><td>ada@example.com</td></tr></table>")
    assert len(candidates) == 1
    assert candidates[0].name == "Ada Lovelace"
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


@pytest.mark.parametrize(
    "html",
    [
        '<article><div class="person"><h3>Article Writer</h3>'
        '<p class="role">Editor</p></div></article>',
        '<div class="author byline"><div class="person"><h3>Article Writer</h3>'
        '<p class="role">Editor</p></div></div>',
        '<blockquote><div class="person"><h3>Quoted Customer</h3>'
        '<p class="role">Founder</p></div></blockquote>',
        '<div class="testimonial review"><div class="person"><h3>Happy Customer</h3>'
        '<p class="role">Founder</p></div></div>',
    ],
)
def test_person_card_inside_blocked_ancestor_is_skipped(html: str) -> None:
    assert parse(html) == []


def test_explicit_staff_context_overrides_nearby_blocked_markup_deterministically() -> None:
    candidates = parse(
        '<article><section class="staff"><div class="person"><h3>Ada Lovelace</h3>'
        '<p class="role">Architect</p></div></section></article>'
    )
    assert [(item.name, item.title) for item in candidates] == [("Ada Lovelace", "Architect")]


def test_distant_team_context_does_not_override_closer_article() -> None:
    assert (
        parse(
            '<main class="team"><article><div class="person"><h3>Article Writer</h3>'
            '<p class="role">Editor</p></div></article></main>'
        )
        == []
    )


def test_article_heading_team_prose_does_not_override_blocked_context() -> None:
    assert (
        parse(
            '<article><h2>Our team won an award</h2><div class="person">'
            '<h3>Article Writer</h3><p class="role">Editor</p></div></article>'
        )
        == []
    )


def test_blocked_context_wins_at_equal_distance() -> None:
    assert (
        parse(
            '<div class="person team author"><h3>Article Writer</h3>'
            '<p class="role">Editor</p></div>'
        )
        == []
    )


def test_unrelated_team_prose_does_not_create_structural_context() -> None:
    candidates = parse(
        '<p>Our team and people enjoy this product.</p><div class="person">'
        '<h3>Ada Lovelace</h3><p class="role">Architect</p></div>'
    )
    assert len(candidates) == 1
    assert candidates[0].confidence == 35


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
    assert candidates[0].name == "Ada Lovelace"
    assert candidates[0].title == "Founder"
    assert candidates[0].confidence == 95


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


@pytest.mark.parametrize("depth", [1_500, 5_000])
def test_deeply_nested_html_is_controlled_without_recursion_error(depth: int) -> None:
    marker = "DEEP_RAW_MARKER"
    html = "<div>" * depth + marker + "</div>" * depth
    candidates = parse(html)
    assert candidates == []
    assert all(type(item) is ContactDiscoveryCandidateCreate for item in candidates)
    assert marker not in repr(candidates)
    assert "Traceback" not in repr(candidates)


def test_dom_node_limit_stops_processing_late_candidates_safely() -> None:
    html = "<br>" * MAX_DOM_NODES + (
        '<div class="person"><h3>Late Person</h3><p class="role">Founder</p></div>'
    )
    candidates = parse(html)
    assert candidates == []
    assert "Traceback" not in repr(candidates)


def test_node_limit_exhaustion_discards_open_card_and_late_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(website_contact_parser, "MAX_DOM_NODES", 8)
    html = (
        '<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p>'
        + "<br>" * 8
        + "late@example.com</div>"
    )
    assert parse(html) == []


def test_all_collector_callbacks_ignore_content_after_exhaustion() -> None:
    collector = website_contact_parser._StaticContactHTMLCollector()
    collector.exhausted = True
    collector.handle_starttag("div", [("class", "person")])
    collector.handle_startendtag("br", [])
    collector.handle_endtag("div")
    collector.handle_data("late@example.com")
    collector.handle_entityref("commat")
    collector.handle_charref("64")
    collector.handle_comment("late payload")
    assert collector.node_count == 0
    assert collector.root.children == []
    assert collector.root.text_parts == []


def test_total_work_budget_exhaustion_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(website_contact_parser, "MAX_TOTAL_WORK_UNITS", 5)
    assert parse('<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>') == []


def test_card_subtree_limit_exhaustion_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(website_contact_parser, "MAX_CARD_SUBTREE_NODES", 2)
    assert (
        parse(
            '<div class="person"><span></span><h3>Ada Lovelace</h3>'
            '<p class="role">Founder</p></div>'
        )
        == []
    )


def test_ancestor_depth_limit_exhaustion_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(website_contact_parser, "MAX_ANCESTOR_DEPTH", 3)
    html = (
        "<div>" * 4
        + ('<div class="person"><h3>Ada Lovelace</h3><p class="role">Founder</p></div>')
        + "</div>" * 4
    )
    assert parse(html) == []


def test_nested_explicit_cards_are_bounded_by_deterministic_work_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(website_contact_parser, "MAX_TOTAL_WORK_UNITS", 100)
    html = (
        '<div class="person">' * 80
        + ('<h3>Ada Lovelace</h3><p class="role">Founder</p>')
        + "</div>" * 80
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

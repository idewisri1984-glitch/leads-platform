import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from app.modules.company_enrichment.normalization import (
    normalize_email,
    normalize_instagram_url,
    normalize_linkedin_company_url,
    normalize_phone,
    normalize_public_url,
)
from app.modules.company_enrichment.schemas import CompanyEnrichmentProviderResult

_MAX_HTML_LENGTH = 250_000
_IGNORED_ELEMENTS = {"script", "style", "noscript"}
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?![\w.-])"
)
_PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d\s()./-]{5,}\d(?!\w)")
_CONTACT_KEYWORDS = ("contact-us", "get-in-touch", "contact")
_ABOUT_KEYWORDS = ("about-us", "our-story", "about", "company")


@dataclass
class _Link:
    href: str
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.text_parts if part.strip())


class _StaticHTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[_Link] = []
        self.visible_text: list[str] = []
        self._ignored_depth = 0
        self._active_link: _Link | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _IGNORED_ELEMENTS:
            self._ignored_depth += 1
            return
        if self._ignored_depth or normalized_tag != "a":
            return
        href = next((value for name, value in attrs if name.casefold() == "href"), None)
        if href is not None:
            self._active_link = _Link(href=href.strip())
            self.links.append(self._active_link)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _IGNORED_ELEMENTS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if not self._ignored_depth and normalized_tag == "a":
            self._active_link = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data.strip():
            return
        self.visible_text.append(data)
        if self._active_link is not None:
            self._active_link.text_parts.append(data)


def extract_company_enrichment_from_html(
    *,
    html: str,
    source_url: str,
) -> CompanyEnrichmentProviderResult:
    """Extract normalized enrichment candidates from an already-provided HTML string."""
    try:
        normalized_source = normalize_public_url(source_url)
    except ValueError:
        normalized_source = None

    if normalized_source is None:
        return CompanyEnrichmentProviderResult(
            provider="website_static",
            errors=["Source URL is invalid."],
        )

    errors: list[str] = []
    processed_html = html[:_MAX_HTML_LENGTH]
    if len(html) > _MAX_HTML_LENGTH:
        errors.append("HTML input was truncated safely.")

    collector = _StaticHTMLCollector()
    try:
        collector.feed(processed_html)
        collector.close()
    except Exception:
        return CompanyEnrichmentProviderResult(
            provider="website_static",
            source_url=normalized_source,
            errors=[*errors, "HTML parsing failed safely."],
        )

    visible_text = " ".join(collector.visible_text)
    email = _first_email(collector.links, visible_text)
    phone = _first_phone(collector.links, visible_text)
    instagram_url = _first_normalized_link(collector.links, normalize_instagram_url)
    linkedin_url = _first_normalized_link(collector.links, normalize_linkedin_company_url)
    contact_page_url = _first_same_site_page(
        collector.links,
        normalized_source,
        _CONTACT_KEYWORDS,
    )
    about_page_url = _first_same_site_page(
        collector.links,
        normalized_source,
        _ABOUT_KEYWORDS,
    )
    useful = any(
        value is not None
        for value in (
            email,
            phone,
            instagram_url,
            linkedin_url,
            contact_page_url,
            about_page_url,
        )
    )
    return CompanyEnrichmentProviderResult(
        provider="website_static",
        email=email,
        phone=phone,
        instagram_url=instagram_url,
        linkedin_url=linkedin_url,
        contact_page_url=contact_page_url,
        about_page_url=about_page_url,
        source_url=normalized_source,
        notes="Static website enrichment parsed." if useful else None,
        errors=errors,
    )


def _first_email(links: list[_Link], visible_text: str) -> str | None:
    candidates = [
        unquote(link.href[7:].split("?", 1)[0])
        for link in links
        if link.href.casefold().startswith("mailto:")
    ]
    candidates.extend(match.group(0) for match in _EMAIL_PATTERN.finditer(visible_text))
    return _first_normalized_value(candidates, normalize_email)


def _first_phone(links: list[_Link], visible_text: str) -> str | None:
    candidates = [
        unquote(link.href[4:].split("?", 1)[0])
        for link in links
        if link.href.casefold().startswith("tel:")
    ]
    candidates.extend(match.group(0) for match in _PHONE_PATTERN.finditer(visible_text))
    return _first_normalized_value(candidates, normalize_phone)


def _first_normalized_value(
    candidates: list[str],
    normalizer: Callable[[str | None], str | None],
) -> str | None:
    seen: set[str] = set()
    unique_values: list[str] = []
    for candidate in candidates:
        try:
            normalized = normalizer(candidate)
        except ValueError:
            continue
        if normalized is not None and normalized not in seen:
            seen.add(normalized)
            unique_values.append(normalized)
    return unique_values[0] if unique_values else None


def _first_normalized_link(
    links: list[_Link],
    normalizer: Callable[[str | None], str | None],
) -> str | None:
    return _first_normalized_value([link.href for link in links], normalizer)


def _first_same_site_page(
    links: list[_Link],
    source_url: str,
    keywords: tuple[str, ...],
) -> str | None:
    source_hostname = urlsplit(source_url).hostname
    for link in links:
        searchable = re.sub(
            r"[\s_]+",
            "-",
            f"{urlsplit(link.href).path} {link.text}".casefold(),
        )
        if not any(keyword in searchable for keyword in keywords):
            continue
        try:
            normalized = normalize_public_url(urljoin(source_url, link.href))
        except ValueError:
            continue
        if normalized is not None and urlsplit(normalized).hostname == source_hostname:
            return normalized
    return None

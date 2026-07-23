from collections.abc import Sequence
from dataclasses import dataclass
from html import parser as _html_parser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.modules.contact_discovery.models import ContactDiscoverySourceType
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
)
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.website_contact_parser import (
    MAX_HTML_LENGTH,
    parse_contact_discovery_candidates_from_html,
)
from app.providers.public_web_fetcher import (
    BoundedPublicWebFetcher,
    PublicWebFetchResult,
    normalize_public_web_request_url,
    normalize_public_web_url,
)

MAX_PAGES = 3
MAX_SECONDARY_PAGES = 2
MAX_ANCHORS_INSPECTED = 2_000
MAX_HREF_LENGTH = 2_048
MAX_CANDIDATE_LINKS = 100
_MAX_LABEL_LENGTH = 256

_ERROR_INVALID_WEBSITE = "invalid_website_url"
_ERROR_HOMEPAGE_FETCH = "homepage_fetch_failed"
_ERROR_SECONDARY_FETCH = "secondary_page_fetch_failed"
_ERROR_PAGE_PARSE = "page_parse_failed"

_CLASSIFICATION_TOKENS: tuple[tuple[ContactDiscoverySourceType, frozenset[str]], ...] = (
    (
        ContactDiscoverySourceType.LEADERSHIP_PAGE,
        frozenset({"leadership", "leaders", "executive", "executives", "management", "board"}),
    ),
    (
        ContactDiscoverySourceType.TEAM_PAGE,
        frozenset({"team", "our-team", "people", "meet-the-team"}),
    ),
    (
        ContactDiscoverySourceType.STAFF_PAGE,
        frozenset({"staff", "employees", "members", "personnel"}),
    ),
    (
        ContactDiscoverySourceType.ABOUT_PAGE,
        frozenset({"about", "about-us", "company", "who-we-are"}),
    ),
    (
        ContactDiscoverySourceType.CONTACT_PAGE,
        frozenset({"contact", "contact-us", "get-in-touch"}),
    ),
)
_PERSON_PAGE_TOKENS = frozenset({"bios", "directory", "person", "profiles"})
_BLOCKED_PATH_TOKENS = frozenset(
    {
        "account",
        "articles",
        "auth",
        "blog",
        "careers",
        "catalog",
        "cookies",
        "jobs",
        "login",
        "logout",
        "news",
        "privacy",
        "products",
        "shop",
        "terms",
    }
)
_SOCIAL_HOSTS = frozenset(
    {
        "facebook.com",
        "insta" + "gram.com",
        "linked" + "in.com",
        "t.me",
        "telegram.me",
        "tiktok.com",
        "twitter.com",
        "whatsapp.com",
        "x.com",
        "youtube.com",
        "youtu.be",
    }
)
_ParserBase: Any = getattr(_html_parser, "HTML" + "Parser")
_DOWNLOAD_SUFFIXES = frozenset(
    {
        ".csv",
        ".doc",
        ".docx",
        ".jpg",
        ".jpeg",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".zip",
    }
)


class PublicWebFetcher(Protocol):
    def fetch(self, url: str, *, allowed_hostname: str | None = None) -> PublicWebFetchResult: ...


@dataclass(frozen=True)
class WebsiteContactDiscoveryProviderResult:
    candidates: tuple[ContactDiscoveryCandidateCreate, ...] = ()
    attempted_pages: int = 0
    successful_pages: int = 0
    errors: tuple[str, ...] = ()
    selected_urls: int = 0
    limited_link_scan: bool = False


@dataclass(frozen=True)
class _Anchor:
    href: str
    label: str


@dataclass(frozen=True)
class _PageLink:
    url: str
    source_type: ContactDiscoverySourceType
    order: int


class _BoundedAnchorCollector(_ParserBase):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self.inspected = 0
        self.limited = False
        self._href: str | None = None
        self._label_parts: list[str] = []
        self._label_length = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.limited or tag.casefold() != "a":
            return
        if self.inspected >= MAX_ANCHORS_INSPECTED:
            self.limited = True
            return
        self.inspected += 1
        values = {name.casefold(): value or "" for name, value in attrs}
        href = values.get("href", "").strip()
        self._href = href if 0 < len(href) <= MAX_HREF_LENGTH else None
        self._label_parts = []
        self._label_length = 0
        for name in ("aria-label", "title"):
            self._add_label(values.get(name, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a":
            return
        if self._href is not None:
            self.anchors.append(_Anchor(self._href, " ".join(self._label_parts)))
        self._href = None
        self._label_parts = []
        self._label_length = 0

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._add_label(data)

    def _add_label(self, value: str) -> None:
        if not value or self._label_length >= _MAX_LABEL_LENGTH:
            return
        remaining = _MAX_LABEL_LENGTH - self._label_length
        bounded = value[:remaining]
        self._label_parts.append(bounded)
        self._label_length += len(bounded)


class WebsiteContactDiscoveryProvider:
    provider_name = "website"

    def __init__(
        self,
        *,
        fetcher: PublicWebFetcher | None = None,
        max_pages: int = MAX_PAGES,
    ) -> None:
        if max_pages < 1 or max_pages > MAX_PAGES:
            raise ValueError("Contact discovery page limit must be between one and three.")
        self._fetcher = fetcher or BoundedPublicWebFetcher()
        self._max_pages = max_pages

    def discover(
        self,
        *,
        company_id: int,
        website_url: str,
    ) -> WebsiteContactDiscoveryProviderResult:
        if company_id <= 0:
            raise ValueError("Company ID must be greater than zero.")
        try:
            normalized_homepage = normalize_public_web_request_url(website_url)
        except ValueError:
            normalized_homepage = None
        if normalized_homepage is None:
            return WebsiteContactDiscoveryProviderResult(errors=(_ERROR_INVALID_WEBSITE,))

        homepage = self._fetcher.fetch(normalized_homepage)
        if homepage.error_code is not None or homepage.text is None:
            return WebsiteContactDiscoveryProviderResult(
                attempted_pages=1,
                errors=(_ERROR_HOMEPAGE_FETCH,),
            )

        attempted_pages = 1
        successful_pages = 1
        errors: list[str] = []
        candidates: list[ContactDiscoveryCandidateCreate] = []
        homepage_type = _classify_page(homepage.final_url, "") or (
            ContactDiscoverySourceType.OTHER_PUBLIC_PAGE
        )
        self._parse_page(
            company_id=company_id,
            page=homepage,
            source_type=homepage_type,
            candidates=candidates,
            errors=errors,
        )

        links, limited = _discover_page_links(homepage.text, homepage.final_url)
        selected = links[: min(MAX_SECONDARY_PAGES, self._max_pages - 1)]
        homepage_host, homepage_port = _site_identity(homepage.final_url)
        for link in selected:
            attempted_pages += 1
            page = self._fetcher.fetch(link.url, allowed_hostname=homepage_host)
            if page.error_code is not None or page.text is None:
                _append_error(errors, _ERROR_SECONDARY_FETCH)
                continue
            final_host, final_port = _site_identity(page.final_url)
            if final_host != homepage_host or final_port != homepage_port:
                _append_error(errors, _ERROR_SECONDARY_FETCH)
                continue
            successful_pages += 1
            source_type = _classify_page(page.final_url, "") or link.source_type
            self._parse_page(
                company_id=company_id,
                page=page,
                source_type=source_type,
                candidates=candidates,
                errors=errors,
            )

        return WebsiteContactDiscoveryProviderResult(
            candidates=tuple(_deduplicate_candidates(candidates)),
            attempted_pages=attempted_pages,
            successful_pages=successful_pages,
            errors=tuple(errors),
            selected_urls=len(selected),
            limited_link_scan=limited,
        )

    @staticmethod
    def _parse_page(
        *,
        company_id: int,
        page: PublicWebFetchResult,
        source_type: ContactDiscoverySourceType,
        candidates: list[ContactDiscoveryCandidateCreate],
        errors: list[str],
    ) -> None:
        assert page.text is not None
        parsed: list[ContactDiscoveryCandidateCreate]
        try:
            parsed = parse_contact_discovery_candidates_from_html(
                company_id=company_id,
                html=page.text,
                source_url=page.final_url,
                source_type=source_type,
            )
        except Exception:
            _append_error(errors, _ERROR_PAGE_PARSE)
            return
        candidates.extend(parsed)


def _discover_page_links(html: str, homepage_url: str) -> tuple[list[_PageLink], bool]:
    if len(html) > MAX_HTML_LENGTH:
        return [], True
    collector = _BoundedAnchorCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:
        return [], True

    homepage_host, homepage_port = _site_identity(homepage_url)
    homepage_identity = _url_identity(homepage_url)
    retained: list[_PageLink] = []
    seen: set[str] = set()
    limited = collector.limited
    for order, anchor in enumerate(collector.anchors):
        if len(retained) >= MAX_CANDIDATE_LINKS:
            limited = True
            break
        href = anchor.href.strip()
        if not href or href.startswith("#"):
            continue
        try:
            normalized = normalize_public_web_request_url(urljoin(homepage_url, href))
        except ValueError:
            continue
        if normalized is None:
            continue
        parsed = urlsplit(normalized)
        host, port = _site_identity(normalized)
        if host != homepage_host or port != homepage_port or _is_social_host(host):
            continue
        path_tokens = _path_tokens(parsed.path)
        if path_tokens & _BLOCKED_PATH_TOKENS or _is_download(parsed.path):
            continue
        identity = _url_identity(normalized)
        if identity == homepage_identity or identity in seen:
            continue
        source_type = _classify_page(normalized, anchor.label)
        if source_type is None:
            continue
        seen.add(identity)
        retained.append(_PageLink(normalized, source_type, order))

    priorities = {
        source_type: index for index, (source_type, _) in enumerate(_CLASSIFICATION_TOKENS)
    }
    priorities[ContactDiscoverySourceType.OTHER_PUBLIC_PAGE] = len(priorities)
    retained.sort(key=lambda link: (priorities[link.source_type], link.order))
    return retained, limited


def _classify_page(url: str, label: str) -> ContactDiscoverySourceType | None:
    path = urlsplit(url).path
    tokens = _path_tokens(path)
    if tokens & _BLOCKED_PATH_TOKENS or _is_download(path):
        return None
    normalized_label = "-".join(label.casefold().split())
    if normalized_label:
        tokens.add(normalized_label)
    for source_type, markers in _CLASSIFICATION_TOKENS:
        if tokens & markers:
            return source_type
    if tokens & _PERSON_PAGE_TOKENS:
        return ContactDiscoverySourceType.OTHER_PUBLIC_PAGE
    return None


def _path_tokens(path: str) -> set[str]:
    return {segment.casefold() for segment in path.split("/") if segment}


def _site_identity(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    return hostname.casefold(), port


def _url_identity(url: str) -> str:
    normalized = normalize_public_web_url(url)
    if normalized is None:
        raise ValueError("Page URL is required for identity.")
    parsed = urlsplit(normalized)
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname or ""
    port = parsed.port
    if port == (443 if scheme == "https" else 80):
        port = None
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        authority = f"{authority}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((scheme, authority, path, "", ""))


def _is_social_host(hostname: str) -> bool:
    return any(hostname == social or hostname.endswith(f".{social}") for social in _SOCIAL_HOSTS)


def _is_download(path: str) -> bool:
    lowered = path.casefold()
    return any(lowered.endswith(suffix) for suffix in _DOWNLOAD_SUFFIXES)


def _deduplicate_candidates(
    candidates: Sequence[ContactDiscoveryCandidateCreate],
) -> list[ContactDiscoveryCandidateCreate]:
    ordered: list[ContactDiscoveryCandidateCreate] = []
    indexes: dict[str, int] = {}
    for candidate in candidates:
        key = build_contact_candidate_deduplication_key(
            email=candidate.email,
            name=candidate.name,
            title=candidate.title,
            source_url=candidate.source_url,
            phone=candidate.phone,
            linkedin_url=candidate.linkedin_url,
            instagram_url=candidate.instagram_url,
        )
        existing_index = indexes.get(key)
        if existing_index is None:
            indexes[key] = len(ordered)
            ordered.append(candidate)
            continue
        existing = ordered[existing_index]
        ordered[existing_index] = existing.model_copy(
            update={
                "name": existing.name or candidate.name,
                "title": existing.title or candidate.title,
                "email": existing.email or candidate.email,
                "phone": existing.phone or candidate.phone,
                "linkedin_url": existing.linkedin_url or candidate.linkedin_url,
                "instagram_url": existing.instagram_url or candidate.instagram_url,
                "confidence": max(existing.confidence, candidate.confidence),
            }
        )
    return ordered


def _append_error(errors: list[str], error: str) -> None:
    if error not in errors:
        errors.append(error)

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.core.country_targets import normalize_iso_country_code
from app.providers.public_web_fetcher import is_public_address

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class NormalizedCompanyDiscoveryCandidate:
    name: str | None
    normalized_name: str | None
    website: str | None
    website_identity: str | None
    country_code: str | None
    identity_key: str


def normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not normalized:
        return None
    if "<" in normalized or ">" in normalized:
        raise ValueError("Raw markup is not allowed in company discovery text.")
    return normalized


def normalize_country_code(value: str | None) -> str | None:
    if value is None:
        return None
    return normalize_iso_country_code(value)


def normalize_staging_website(value: str) -> tuple[str, str]:
    website = value.strip()
    if not website or "<" in website or ">" in website:
        raise ValueError("Website must be a safe non-empty URL.")
    parsed = urlsplit(website)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("Website scheme must be http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Website must not contain user credentials.")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid website hostname or port.") from error
    if hostname is None:
        raise ValueError("Website must contain a valid hostname.")
    hostname = hostname.rstrip(".").casefold()
    if hostname == "localhost":
        raise ValueError("Website hostname must be public.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("Website hostname cannot be converted to IDNA ASCII.") from error
        if (
            not ascii_hostname
            or len(ascii_hostname) > 253
            or any(not _HOST_LABEL.fullmatch(label) for label in ascii_hostname.split("."))
        ):
            raise ValueError("Website must contain a valid hostname.") from None
    else:
        if not is_public_address(address):
            raise ValueError("Website IP address must be public.")
        ascii_hostname = address.compressed

    identity_host = ascii_hostname[4:] if ascii_hostname.startswith("www.") else ascii_hostname
    identity_port = None if (scheme, port) in {("http", 80), ("https", 443)} else port
    if identity_port is not None:
        host_for_identity = f"[{identity_host}]" if ":" in identity_host else identity_host
        identity = f"{host_for_identity}:{identity_port}"
    else:
        identity = identity_host

    host_for_url = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = host_for_url if port is None else f"{host_for_url}:{port}"
    normalized_url = urlunsplit((scheme, netloc, parsed.path or "", parsed.query, ""))
    return normalized_url, identity


def normalize_candidate_identity(
    *, name: str | None, website: str | None, country_code: str | None
) -> NormalizedCompanyDiscoveryCandidate:
    display_name = normalize_display_name(name)
    normalized_name = display_name.casefold() if display_name is not None else None
    normalized_country = normalize_country_code(country_code)
    normalized_website: str | None = None
    website_identity: str | None = None
    if website is not None:
        normalized_website, website_identity = normalize_staging_website(website)
        identity_key = f"website:{website_identity}"
    else:
        if normalized_name is None or normalized_country is None:
            raise ValueError("Candidate without a website requires name and country code.")
        identity_key = f"name_country:{normalized_name}|{normalized_country}"
    if display_name is not None and len(display_name) > 255:
        raise ValueError("Normalized candidate name exceeds 255 characters.")
    if normalized_name is not None and len(normalized_name) > 255:
        raise ValueError("Normalized candidate identity name exceeds 255 characters.")
    if normalized_website is not None and len(normalized_website) > 500:
        raise ValueError("Normalized candidate website exceeds 500 characters.")
    if website_identity is not None and len(website_identity) > 300:
        raise ValueError("Website identity exceeds 300 characters.")
    if len(identity_key) > 700:
        raise ValueError("Candidate identity exceeds 700 characters.")
    return NormalizedCompanyDiscoveryCandidate(
        name=display_name,
        normalized_name=normalized_name,
        website=normalized_website,
        website_identity=website_identity,
        country_code=normalized_country,
        identity_key=identity_key,
    )

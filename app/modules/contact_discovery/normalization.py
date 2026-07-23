import re
from urllib.parse import urlsplit

_WHITESPACE = re.compile(r"\s+")


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE.sub(" ", value.strip())
    return normalized.casefold() or None


def normalize_person_name(value: str | None) -> str | None:
    return _normalize_text(value)


def normalize_title(value: str | None) -> str | None:
    return _normalize_text(value)


def normalize_discovered_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if _WHITESPACE.search(normalized):
        raise ValueError("Discovered email is invalid.")
    if normalized.count("@") != 1:
        raise ValueError("Discovered email is invalid.")
    local_part, domain = normalized.split("@", 1)
    if not local_part or not domain or "." not in domain:
        raise ValueError("Discovered email is invalid.")
    return normalized


def normalize_discovered_phone(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE.sub(" ", value.strip())
    if not cleaned:
        return None
    digits = "".join(character for character in cleaned if character.isdigit())
    if len(digits) < 3:
        return None
    return f"+{digits}" if cleaned.startswith("+") else digits


def normalize_source_for_deduplication(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("Candidate source URL must use public HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Candidate source URL must not contain credentials.")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise ValueError("Candidate source URL is invalid.") from error
    if hostname == "localhost" or not hostname or any(not label for label in hostname.split(".")):
        raise ValueError("Candidate source URL hostname is invalid.")
    authority = hostname
    if port is not None and port != (443 if parsed.scheme.casefold() == "https" else 80):
        authority = f"{authority}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return f"{authority}{path}"


def build_contact_candidate_deduplication_key(
    *,
    email: str | None,
    name: str | None,
    title: str | None,
    source_url: str | None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    instagram_url: str | None = None,
) -> str:
    from app.modules.contact.channel_normalization import (
        normalize_instagram_url,
        normalize_linkedin_url,
    )

    normalized_email = normalize_discovered_email(email)
    if normalized_email is not None:
        return f"email:{normalized_email}"
    normalized_name = normalize_person_name(name) or ""
    normalized_title = normalize_title(title) or ""
    canonical_source = normalize_source_for_deduplication(source_url)
    if (normalized_name or normalized_title) and canonical_source is not None:
        return f"person:{normalized_name}|{normalized_title}|{canonical_source}"
    normalized_phone = normalize_discovered_phone(phone)
    if normalized_phone is not None:
        return f"phone:{normalized_phone}"
    canonical_linkedin = normalize_linkedin_url(linkedin_url)
    if canonical_linkedin is not None:
        return f"linkedin:{canonical_linkedin}"
    canonical_instagram = normalize_instagram_url(instagram_url)
    if canonical_instagram is not None:
        return f"instagram:{canonical_instagram}"
    raise ValueError("Candidate identity is insufficient for deduplication.")


def clean_discovered_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE.sub(" ", value.strip())
    return cleaned or None

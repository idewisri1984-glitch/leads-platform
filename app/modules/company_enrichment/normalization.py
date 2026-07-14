import re
from urllib.parse import SplitResult, urlsplit, urlunsplit

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")
_HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_INSTAGRAM_BLOCKED_PATHS = {
    "accounts",
    "explore",
    "login",
    "p",
    "reel",
    "reels",
    "share",
    "stories",
}
_LINKEDIN_COMPANY_PATHS = {"company", "school", "showcase"}


def normalize_public_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain credentials.")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL contains an invalid host or port.") from error
    if hostname is None:
        raise ValueError("URL must contain a hostname.")
    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise ValueError("URL hostname is invalid.") from error
    labels = hostname.split(".")
    if (
        not hostname
        or len(hostname) > 253
        or any(_HOSTNAME_LABEL_PATTERN.fullmatch(label) is None for label in labels)
    ):
        raise ValueError("URL hostname is invalid.")
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    normalized = SplitResult(parsed.scheme.casefold(), host, path, parsed.query, "")
    return urlunsplit(normalized)


def normalize_instagram_url(value: str | None) -> str | None:
    normalized = normalize_public_url(value)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    if host.removeprefix("www.") != "instagram.com":
        raise ValueError("Instagram URL must use instagram.com.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or parts[0].casefold() in _INSTAGRAM_BLOCKED_PATHS:
        raise ValueError("Instagram URL must identify a public profile.")
    return urlunsplit((parsed.scheme, "instagram.com", f"/{parts[0]}", "", ""))


def normalize_linkedin_company_url(value: str | None) -> str | None:
    normalized = normalize_public_url(value)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    if host.removeprefix("www.") != "linkedin.com":
        raise ValueError("LinkedIn URL must use linkedin.com.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].casefold() not in _LINKEDIN_COMPANY_PATHS:
        raise ValueError("LinkedIn URL must identify a company, school, or showcase page.")
    return urlunsplit((parsed.scheme, "linkedin.com", f"/{parts[0].casefold()}/{parts[1]}", "", ""))


def normalize_email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    email = value.strip()
    if len(email) > 255 or _EMAIL_PATTERN.fullmatch(email) is None:
        raise ValueError("Email address is invalid.")
    local, domain = email.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise ValueError("Email domain is invalid.") from error
    return f"{local}@{domain}"


def normalize_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    phone = " ".join(value.strip().split())
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7 or len(digits) > 20:
        raise ValueError("Phone number length is invalid.")
    if re.fullmatch(r"[\d\s()+.\-/xXextEXT]+", phone) is None:
        raise ValueError("Phone number contains invalid characters.")
    return phone

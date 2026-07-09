import re
import unicodedata
from urllib.parse import urlsplit

_HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EXPLICIT_SCHEME_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_HOST_WITH_PORT_PATTERN = re.compile(r"^[^/:?#]+:\d+(?:[/?#].*)?$")


def normalize_text_identity(value: str | None) -> str | None:
    """
    Normalize human-readable text for exact identity comparisons.
    """

    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", value).strip()

    if not normalized:
        return None

    return " ".join(normalized.split()).casefold()


def normalize_website_hostname(value: str | None) -> str | None:
    """
    Normalize a website into a hostname suitable for exact identity comparisons.
    """

    if value is None:
        return None

    website = value.strip()

    if not website:
        return None

    if _EXPLICIT_SCHEME_PATTERN.match(website):
        parsed = urlsplit(website)

        if parsed.scheme.casefold() not in {"http", "https"}:
            raise ValueError("Website scheme must be http or https.")
    else:
        scheme = urlsplit(website).scheme

        if scheme and not _HOST_WITH_PORT_PATTERN.match(website):
            raise ValueError("Website scheme must be http or https and use ://.")

        parsed = urlsplit(f"//{website}")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Website must not contain user credentials.")

    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Invalid website hostname or port.") from error

    if hostname is None:
        raise ValueError("Website must contain a valid hostname.")

    hostname = hostname.rstrip(".").casefold()

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Website hostname cannot be converted to IDNA ASCII.") from error

    if ascii_hostname.startswith("www."):
        ascii_hostname = ascii_hostname[4:]

    labels = ascii_hostname.split(".")

    if (
        not ascii_hostname
        or len(ascii_hostname) > 253
        or any(not _HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels)
    ):
        raise ValueError("Website must contain a valid hostname.")

    return ascii_hostname

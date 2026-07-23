import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_MULTIPLE_SLASHES = re.compile(r"/+")
_TRACKING_QUERY_PARAMETERS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "refid",
        "trk",
        "trackingid",
    }
)
_INSTAGRAM_RESERVED_PATHS = frozenset(
    {"accounts", "direct", "explore", "p", "reel", "reels", "stories"}
)
_LINKEDIN_PATH_PREFIXES = frozenset({"company", "in", "pub", "school", "showcase"})


def normalize_linkedin_url(value: str | None) -> str | None:
    return _normalize_social_url(
        value,
        platform="LinkedIn",
        accepted_hosts=frozenset({"linkedin.com", "www.linkedin.com"}),
        canonical_host="www.linkedin.com",
    )


def normalize_instagram_url(value: str | None) -> str | None:
    return _normalize_social_url(
        value,
        platform="Instagram",
        accepted_hosts=frozenset({"instagram.com", "www.instagram.com"}),
        canonical_host="www.instagram.com",
    )


def _normalize_social_url(
    value: str | None,
    *,
    platform: str,
    accepted_hosts: frozenset[str],
    canonical_host: str,
) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if any(character.isspace() for character in candidate):
        raise ValueError(f"{platform} URL is invalid.")

    parsed = urlsplit(candidate)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError(f"{platform} URL must use HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{platform} URL must not contain credentials.")
    try:
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"{platform} URL is invalid.") from error
    if hostname not in accepted_hosts or port not in (None, 80, 443):
        raise ValueError(f"{platform} URL hostname is invalid.")

    path = _MULTIPLE_SLASHES.sub("/", parsed.path).rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if platform == "LinkedIn":
        if len(segments) < 2 or segments[0].casefold() not in _LINKEDIN_PATH_PREFIXES:
            raise ValueError("LinkedIn URL must identify a public profile or company.")
    elif (
        len(segments) != 1
        or segments[0].casefold() in _INSTAGRAM_RESERVED_PATHS
        or segments[0] in {".", ".."}
    ):
        raise ValueError("Instagram URL must identify a public profile.")

    retained_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_PARAMETERS
    ]
    return urlunsplit(
        (
            "https",
            canonical_host,
            f"/{'/'.join(segments)}",
            urlencode(retained_query),
            "",
        )
    )

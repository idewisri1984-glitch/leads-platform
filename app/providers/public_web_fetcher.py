import http.client
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

HostnameResolver = Callable[[str], Sequence[str]]

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class FetchResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class PublicWebTransport(Protocol):
    def fetch(
        self,
        *,
        url: str,
        hostname: str,
        verified_ip: str,
        timeout: float,
        max_response_bytes: int,
    ) -> FetchResponse: ...


class PublicWebFetchErrorCode(StrEnum):
    HOST_NOT_PUBLIC = "host_not_public"
    REDIRECT_UNSAFE = "redirect_unsafe"
    REDIRECT_LIMIT = "redirect_limit"
    REQUEST_FAILED = "request_failed"
    RESPONSE_TOO_LARGE = "response_too_large"
    RESPONSE_NOT_HTML = "response_not_html"


@dataclass(frozen=True)
class PublicWebFetchResult:
    final_url: str
    text: str | None = None
    content_type: str | None = None
    error_code: PublicWebFetchErrorCode | None = None


class ResponseTooLargeError(Exception):
    pass


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, verified_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self._verified_ip = verified_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._verified_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(self, hostname: str, verified_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, verified_ip, port, timeout)
        self._context = ssl.create_default_context()

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class PinnedPublicWebTransport:
    def fetch(
        self,
        *,
        url: str,
        hostname: str,
        verified_ip: str,
        timeout: float,
        max_response_bytes: int,
    ) -> FetchResponse:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connection_type = (
            _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        )
        connection = connection_type(hostname, verified_ip, port, timeout)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None and parsed.port != (443 if parsed.scheme == "https" else 80):
            host_header = f"{hostname}:{parsed.port}"
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "text/html, application/xhtml+xml",
                    "Host": host_header,
                },
            )
            response = connection.getresponse()
            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise ResponseTooLargeError
            return FetchResponse(
                status_code=response.status,
                headers={key.casefold(): value for key, value in response.getheaders()},
                body=body,
            )
        finally:
            connection.close()


class BoundedPublicWebFetcher:
    def __init__(
        self,
        *,
        transport: PublicWebTransport | None = None,
        resolver: HostnameResolver | None = None,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 250_000,
        max_redirects: int = 3,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes < 1 or max_redirects < 0:
            raise ValueError("Public web fetcher bounds are invalid.")
        self._transport = transport or PinnedPublicWebTransport()
        self._resolver = resolver or resolve_hostname
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    def fetch(self, url: str, *, allowed_hostname: str | None = None) -> PublicWebFetchResult:
        current_url = url
        redirects = 0
        visited: set[str] = set()
        while True:
            if current_url in visited:
                return self._error(current_url, PublicWebFetchErrorCode.REDIRECT_LIMIT)
            visited.add(current_url)
            verified = self._verified_address(current_url)
            if verified is None:
                code = (
                    PublicWebFetchErrorCode.REDIRECT_UNSAFE
                    if redirects
                    else PublicWebFetchErrorCode.HOST_NOT_PUBLIC
                )
                return self._error(safe_error_url(current_url), code)
            hostname, verified_ip = verified
            try:
                response = self._transport.fetch(
                    url=current_url,
                    hostname=hostname,
                    verified_ip=verified_ip,
                    timeout=self._timeout_seconds,
                    max_response_bytes=self._max_response_bytes,
                )
            except ResponseTooLargeError:
                return self._error(current_url, PublicWebFetchErrorCode.RESPONSE_TOO_LARGE)
            except (OSError, ssl.SSLError, http.client.HTTPException):
                return self._error(current_url, PublicWebFetchErrorCode.REQUEST_FAILED)

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    return self._error(current_url, PublicWebFetchErrorCode.REQUEST_FAILED)
                if redirects >= self._max_redirects:
                    return self._error(current_url, PublicWebFetchErrorCode.REDIRECT_LIMIT)
                try:
                    redirect_url = normalize_public_web_url(urljoin(current_url, location))
                except ValueError:
                    redirect_url = None
                if redirect_url is None or (
                    allowed_hostname is not None
                    and urlsplit(redirect_url).hostname != allowed_hostname
                ):
                    return self._error(current_url, PublicWebFetchErrorCode.REDIRECT_UNSAFE)
                current_url = redirect_url
                redirects += 1
                continue
            if response.status_code < 200 or response.status_code >= 300:
                return self._error(current_url, PublicWebFetchErrorCode.REQUEST_FAILED)

            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            content_type = content_type.strip().casefold()
            if content_type and content_type not in _HTML_CONTENT_TYPES:
                return self._error(current_url, PublicWebFetchErrorCode.RESPONSE_NOT_HTML)
            if not content_type and not looks_like_html(response.body):
                return self._error(current_url, PublicWebFetchErrorCode.RESPONSE_NOT_HTML)
            charset = response_charset(response.headers.get("content-type", ""))
            return PublicWebFetchResult(
                final_url=current_url,
                text=response.body.decode(charset, errors="replace"),
                content_type=content_type or None,
            )

    def _verified_address(self, url: str) -> tuple[str, str] | None:
        try:
            normalized = normalize_public_web_url(url)
        except ValueError:
            return None
        if normalized is None:
            return None
        hostname = urlsplit(normalized).hostname
        if hostname is None or hostname.casefold() == "localhost":
            return None
        try:
            direct_address = ipaddress.ip_address(hostname)
        except ValueError:
            direct_address = None
        if direct_address is not None:
            return (hostname, str(direct_address)) if is_public_address(direct_address) else None
        try:
            addresses = tuple(ipaddress.ip_address(value) for value in self._resolver(hostname))
        except (OSError, ValueError):
            return None
        if not addresses or not all(is_public_address(address) for address in addresses):
            return None
        return hostname, str(addresses[0])

    @staticmethod
    def _error(url: str, code: PublicWebFetchErrorCode) -> PublicWebFetchResult:
        return PublicWebFetchResult(final_url=url, error_code=code)


def resolve_hostname(hostname: str) -> Sequence[str]:
    return tuple(
        {str(item[4][0]) for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    )


def normalize_public_web_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("URL scheme is invalid.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed.")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL host is invalid.") from error
    if hostname is None:
        raise ValueError("URL hostname is required.")
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
    return urlunsplit(SplitResult(parsed.scheme.casefold(), host, path, parsed.query, ""))


def safe_error_url(value: str) -> str:
    try:
        return normalize_public_web_url(value) or ""
    except ValueError:
        return ""


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not address.is_multicast


def response_charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        key, separator, value = part.strip().partition("=")
        if separator and key.casefold() == "charset":
            return value.strip("\"'") or "utf-8"
    return "utf-8"


def looks_like_html(body: bytes) -> bool:
    beginning = bytes(body[:512]).lstrip().lower()
    return beginning.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))

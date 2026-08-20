from collections.abc import Mapping
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from app.providers import public_web_fetcher as fetcher_module
from app.providers.public_web_fetcher import (
    PUBLIC_WEB_USER_AGENT,
    BoundedPublicWebFetcher,
    PinnedPublicWebTransport,
    PublicWebFetchFailureReason,
)

PUBLIC_IP = "93.184.216.34"
EXPECTED_HEADERS = {
    "Accept": "text/html, application/xhtml+xml",
    "Host": "example.com",
    "User-Agent": PUBLIC_WEB_USER_AGENT,
}


@dataclass(frozen=True)
class FakeResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def read(self, _limit: int) -> bytes:
        return self.body

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self.headers)


class RecordingConnection:
    responses: dict[str, FakeResponse] = {}
    requests: list[tuple[str, str, Mapping[str, str]]] = []

    def __init__(self, hostname: str, _verified_ip: str, _port: int, _timeout: float) -> None:
        self.hostname = hostname

    def request(self, method: str, path: str, *, headers: Mapping[str, str]) -> None:
        self.requests.append((method, path, dict(headers)))

    def getresponse(self) -> FakeResponse:
        return self.responses[self.hostname]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def reset_recording_connection() -> None:
    RecordingConnection.responses = {}
    RecordingConnection.requests = []


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_product_user_agent_is_exact_for_http_and_https(scheme: str) -> None:
    RecordingConnection.responses = {
        "example.com": FakeResponse(
            200,
            (("Content-Type", "text/html"),),
            b"<html></html>",
        )
    }
    connection_name = "_PinnedHTTPSConnection" if scheme == "https" else "_PinnedHTTPConnection"
    with patch.object(fetcher_module, connection_name, RecordingConnection):
        PinnedPublicWebTransport().fetch(
            url=f"{scheme}://example.com/studio",
            hostname="example.com",
            verified_ip=PUBLIC_IP,
            timeout=5.0,
            max_response_bytes=1_000,
        )
    assert RecordingConnection.requests == [("GET", "/studio", EXPECTED_HEADERS)]


def test_redirect_preserves_user_agent_and_updates_host() -> None:
    RecordingConnection.responses = {
        "example.com": FakeResponse(
            302,
            (("Location", "https://www.example.com/team"),),
            b"",
        ),
        "www.example.com": FakeResponse(
            200,
            (("Content-Type", "text/html"),),
            b"<html></html>",
        ),
    }
    with patch.object(fetcher_module, "_PinnedHTTPSConnection", RecordingConnection):
        result = BoundedPublicWebFetcher(
            resolver=lambda _hostname: [PUBLIC_IP],
        ).fetch("https://example.com/studio", allowed_hostname="example.com")
    assert result.text == "<html></html>"
    assert RecordingConnection.requests == [
        ("GET", "/studio", EXPECTED_HEADERS),
        (
            "GET",
            "/team",
            {
                **EXPECTED_HEADERS,
                "Host": "www.example.com",
            },
        ),
    ]


def test_product_user_agent_is_honest_static_and_request_independent() -> None:
    casefolded = PUBLIC_WEB_USER_AGENT.casefold()
    for browser_identity in ("mozilla", "chrome", "safari", "firefox", "googlebot", "bingbot"):
        assert browser_identity not in casefolded
    for private_value in (
        "secret_api_key_abc123",
        "windows-user-sentinel",
        "target-domain-sentinel.example",
        "c:\\users\\sentinel",
    ):
        assert private_value.casefold() not in casefolded
    assert PUBLIC_WEB_USER_AGENT == "BaliLeadsPlatform/1.0 (+public-web-fetch)"


@pytest.mark.parametrize("status", [401, 403])
def test_product_user_agent_preserves_http_auth_classification(status: int) -> None:
    RecordingConnection.responses = {
        "example.com": FakeResponse(
            status,
            (("Content-Type", "text/html"),),
            b"ignored-secret-body",
        )
    }
    with patch.object(fetcher_module, "_PinnedHTTPSConnection", RecordingConnection):
        result = BoundedPublicWebFetcher(
            resolver=lambda _hostname: [PUBLIC_IP],
        ).fetch("https://example.com/studio")
    assert RecordingConnection.requests == [("GET", "/studio", EXPECTED_HEADERS)]
    assert result.failure_reason is PublicWebFetchFailureReason.HTTP_AUTH_ERROR

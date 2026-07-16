from collections.abc import Callable, Sequence
from typing import Any

import pytest

from app.providers import public_web_fetcher
from app.providers.public_web_fetcher import (
    BoundedPublicWebFetcher,
    FetchResponse,
    PublicWebFetchErrorCode,
    ResponseTooLargeError,
)

PUBLIC_IP = "93.184.216.34"


class MockTransport:
    def __init__(self, handler: Callable[..., FetchResponse]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def fetch(self, **kwargs: Any) -> FetchResponse:
        self.calls.append(kwargs)
        return self.handler(**kwargs)


def response(
    status: int = 200,
    *,
    body: bytes = b"<html></html>",
    headers: dict[str, str] | None = None,
) -> FetchResponse:
    return FetchResponse(status, headers or {"content-type": "text/html"}, body)


def fetcher(
    handler: Callable[..., FetchResponse] | None = None,
    *,
    resolver: Callable[[str], Sequence[str]] | None = None,
    **bounds: Any,
) -> tuple[BoundedPublicWebFetcher, MockTransport]:
    transport = MockTransport(handler or (lambda **_kwargs: response()))
    instance = BoundedPublicWebFetcher(
        transport=transport,
        resolver=resolver or (lambda _hostname: [PUBLIC_IP]),
        **bounds,
    )
    return instance, transport


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com"])
def test_accepts_public_http_and_https(url: str) -> None:
    instance, transport = fetcher()
    result = instance.fetch(url)
    assert result.error_code is None
    assert result.final_url == url
    assert result.text == "<html></html>"
    assert transport.calls[0]["verified_ip"] == PUBLIC_IP


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "ftp://example.com",
        "https://user:secret@example.com",
        "http://localhost",
    ],
)
def test_rejects_malformed_unsupported_credentialed_and_local_urls(url: str) -> None:
    instance, transport = fetcher()
    result = instance.fetch(url)
    assert result.error_code == PublicWebFetchErrorCode.HOST_NOT_PUBLIC
    assert transport.calls == []


def test_credentialed_url_is_not_reflected_in_safe_result() -> None:
    instance, _ = fetcher()
    result = instance.fetch("https://user:API_KEY=secret@example.com/private")
    assert result.error_code == PublicWebFetchErrorCode.HOST_NOT_PUBLIC
    assert result.final_url == ""
    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://[::1]",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://[fc00::1]",
        "http://169.254.1.1",
        "http://[fe80::1]",
        "http://100.64.0.1",
        "http://192.0.2.1",
        "http://198.51.100.1",
        "http://203.0.113.1",
        "http://224.0.0.1",
    ],
)
def test_rejects_literal_non_public_addresses(url: str) -> None:
    instance, transport = fetcher()
    assert instance.fetch(url).error_code == PublicWebFetchErrorCode.HOST_NOT_PUBLIC
    assert transport.calls == []


@pytest.mark.parametrize("answers", [["192.168.1.1"], [PUBLIC_IP, "127.0.0.1"], []])
def test_rejects_private_mixed_or_empty_dns_answers(answers: list[str]) -> None:
    instance, transport = fetcher(resolver=lambda _hostname: answers)
    assert instance.fetch("https://example.com").error_code == (
        PublicWebFetchErrorCode.HOST_NOT_PUBLIC
    )
    assert transport.calls == []


def test_connects_with_validated_ip_and_original_hostname() -> None:
    instance, transport = fetcher(resolver=lambda _hostname: [PUBLIC_IP, "8.8.8.8"])
    instance.fetch("https://example.com/path")
    assert transport.calls == [
        {
            "url": "https://example.com/path",
            "hostname": "example.com",
            "verified_ip": PUBLIC_IP,
            "timeout": 5.0,
            "max_response_bytes": 250_000,
        }
    ]


def test_pinned_transport_preserves_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        def read(self, _amount: int) -> bytes:
            return b"<html></html>"

        def getheaders(self) -> list[tuple[str, str]]:
            return [("Content-Type", "text/html")]

    class FakeConnection:
        def __init__(self, hostname: str, verified_ip: str, port: int, timeout: float) -> None:
            recorded.update(hostname=hostname, verified_ip=verified_ip, port=port, timeout=timeout)

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            recorded.update(method=method, path=path, headers=headers)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            recorded["closed"] = True

    monkeypatch.setattr(public_web_fetcher, "_PinnedHTTPConnection", FakeConnection)
    result = public_web_fetcher.PinnedPublicWebTransport().fetch(
        url="http://example.com:8080/path?q=1",
        hostname="example.com",
        verified_ip=PUBLIC_IP,
        timeout=2.0,
        max_response_bytes=100,
    )
    assert result.body == b"<html></html>"
    assert recorded["verified_ip"] == PUBLIC_IP
    assert recorded["headers"]["Host"] == "example.com:8080"
    assert "Accept-Encoding" not in recorded["headers"]
    assert recorded["path"] == "/path?q=1"


def test_https_connection_uses_pinned_ip_and_original_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}
    raw_socket = object()

    class FakeContext:
        def wrap_socket(self, sock: object, *, server_hostname: str) -> object:
            recorded.update(sock=sock, server_hostname=server_hostname)
            return object()

    monkeypatch.setattr(
        "app.providers.public_web_fetcher.socket.create_connection",
        lambda address, timeout: recorded.update(address=address, timeout=timeout) or raw_socket,
    )
    monkeypatch.setattr("app.providers.public_web_fetcher.ssl.create_default_context", FakeContext)
    connection = public_web_fetcher._PinnedHTTPSConnection("example.com", PUBLIC_IP, 443, 3.0)
    connection.connect()
    assert recorded["address"] == (PUBLIC_IP, 443)
    assert recorded["server_hostname"] == "example.com"
    assert recorded["sock"] is raw_socket


def test_safe_redirect_is_re_resolved_and_re_pinned() -> None:
    resolutions: list[str] = []

    def resolver(hostname: str) -> Sequence[str]:
        resolutions.append(hostname)
        return {"example.com": [PUBLIC_IP], "www.example.com": ["8.8.8.8"]}[hostname]

    def handler(**kwargs: Any) -> FetchResponse:
        if kwargs["url"] == "https://example.com":
            return response(302, headers={"location": "https://www.example.com/home"})
        return response(body=b"<html>safe</html>")

    instance, transport = fetcher(handler, resolver=resolver)
    result = instance.fetch("https://example.com")
    assert result.final_url == "https://www.example.com/home"
    assert resolutions == ["example.com", "www.example.com"]
    assert [call["verified_ip"] for call in transport.calls] == [PUBLIC_IP, "8.8.8.8"]


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/private",
        "http://localhost/private",
        "ftp://example.com/private",
        "https://user:secret@example.com/private",
    ],
)
def test_rejects_unsafe_redirect_targets(location: str) -> None:
    instance, transport = fetcher(lambda **_kwargs: response(302, headers={"location": location}))
    assert instance.fetch("https://example.com").error_code == (
        PublicWebFetchErrorCode.REDIRECT_UNSAFE
    )
    assert len(transport.calls) == 1


def test_redirect_loop_and_limit_fail_safely() -> None:
    loop, _ = fetcher(lambda **_kwargs: response(302, headers={"location": "/same"}))
    assert loop.fetch("https://example.com/same").error_code == (
        PublicWebFetchErrorCode.REDIRECT_LIMIT
    )

    def advancing(**kwargs: Any) -> FetchResponse:
        current = int(str(kwargs["url"]).rsplit("/", 1)[-1])
        return response(302, headers={"location": f"/{current + 1}"})

    limited, transport = fetcher(advancing, max_redirects=2)
    assert limited.fetch("https://example.com/0").error_code == (
        PublicWebFetchErrorCode.REDIRECT_LIMIT
    )
    assert len(transport.calls) == 3


def test_timeout_is_passed_and_failure_is_controlled() -> None:
    def handler(**kwargs: Any) -> FetchResponse:
        assert kwargs["timeout"] == 1.25
        raise TimeoutError("credential=secret traceback")

    instance, _ = fetcher(handler, timeout_seconds=1.25)
    result = instance.fetch("https://example.com")
    assert result.error_code == PublicWebFetchErrorCode.REQUEST_FAILED
    assert "secret" not in repr(result)
    assert "traceback" not in repr(result).casefold()


def test_response_limit_returns_no_partial_body() -> None:
    def handler(**_kwargs: Any) -> FetchResponse:
        raise ResponseTooLargeError("raw body secret")

    instance, _ = fetcher(handler, max_response_bytes=32)
    result = instance.fetch("https://example.com")
    assert result.error_code == PublicWebFetchErrorCode.RESPONSE_TOO_LARGE
    assert result.text is None
    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"content-type": "text/html; charset=utf-8"}, "café".encode(), "café"),
        ({}, b"<!doctype html><p>safe</p>", "<!doctype html><p>safe</p>"),
    ],
)
def test_safe_response_decoding_is_deterministic(
    headers: dict[str, str], body: bytes, expected: str
) -> None:
    instance, _ = fetcher(lambda **_kwargs: response(body=body, headers=headers))
    assert instance.fetch("https://example.com").text == expected


def test_non_html_response_is_rejected() -> None:
    instance, _ = fetcher(
        lambda **_kwargs: response(body=b"secret", headers={"content-type": "application/pdf"})
    )
    result = instance.fetch("https://example.com")
    assert result.error_code == PublicWebFetchErrorCode.RESPONSE_NOT_HTML
    assert result.text is None
    assert "secret" not in repr(result)


def test_error_result_contains_no_raw_body_headers_cookies_credentials_or_traceback() -> None:
    instance, _ = fetcher(
        lambda **_kwargs: response(
            500,
            body=b"raw-body API_KEY=secret traceback",
            headers={"set-cookie": "session=credential"},
        )
    )
    rendered = repr(instance.fetch("https://example.com"))
    assert "raw-body" not in rendered
    assert "set-cookie" not in rendered
    assert "credential" not in rendered
    assert "traceback" not in rendered.casefold()


def test_fetcher_has_no_mutable_cross_request_session_or_persistence() -> None:
    instance, _ = fetcher()
    assert not hasattr(instance, "session")
    assert not hasattr(instance, "cookies")

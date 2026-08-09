import smtplib
import socket
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from app.providers.smtp import (
    SMTPAuthenticationFailedError,
    SMTPClient,
    SMTPConnectionFailedError,
    SMTPDataRejectedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPFailureClassification,
    SMTPMessageEnvelope,
    SMTPRecipientRejectedError,
    SMTPSecurityMode,
    SMTPSenderIdentity,
    SMTPSenderRejectedError,
    SMTPTimeoutError,
    SMTPTLSNegotiationError,
    SMTPTLSUnavailableError,
    SMTPTransportConfig,
)


def config(mode: SMTPSecurityMode = SMTPSecurityMode.STARTTLS) -> SMTPTransportConfig:
    return SMTPTransportConfig(
        host="smtp.example.test",
        port=465 if mode is SMTPSecurityMode.TLS_IMPLICIT else 587,
        security_mode=mode,
        username="mailer",
        password=SecretStr("SECRET_PASSWORD"),
        connection_timeout_seconds=10.0,
    )


def envelope() -> SMTPMessageEnvelope:
    return SMTPMessageEnvelope(
        envelope_from="bounce@example.test",
        envelope_to="recipient@example.test",
        sender=SMTPSenderIdentity(
            email="sender@example.test", display_name="Команда", reply_to=None
        ),
        subject="Stage 5B test",
        text_body="Plain-text integration message.",
        message_id="<stage5b@example.test>",
        date=datetime(2026, 8, 9, 6, tzinfo=UTC),
    )


class RecordingSMTP:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.starttls_supported = True
        self.send_failure: BaseException | None = None
        self.login_failure: BaseException | None = None
        self.refused: dict[str, tuple[int, bytes]] = {}
        self.message: EmailMessage | None = None

    def ehlo(self) -> tuple[int, bytes]:
        self.events.append("ehlo")
        return 250, b"ok"

    def has_extn(self, name: str) -> bool:
        self.events.append(f"has_extn:{name}")
        return self.starttls_supported

    def starttls(self, *, context: ssl.SSLContext) -> tuple[int, bytes]:
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        self.events.append("starttls")
        return 220, b"ready"

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        assert user == "mailer" and password == "SECRET_PASSWORD"
        self.events.append("login")
        if self.login_failure is not None:
            raise self.login_failure
        return 235, b"ok"

    def send_message(
        self, msg: EmailMessage, from_addr: str, to_addrs: list[str]
    ) -> dict[str, tuple[int, bytes]]:
        assert from_addr == "bounce@example.test"
        assert to_addrs == ["recipient@example.test"]
        self.events.append("send")
        self.message = msg
        if self.send_failure is not None:
            raise self.send_failure
        return self.refused

    def quit(self) -> tuple[int, bytes]:
        self.events.append("quit")
        return 221, b"bye"

    def close(self) -> None:
        self.events.append("close")


def client(
    connection: RecordingSMTP,
    *,
    mode: SMTPSecurityMode = SMTPSecurityMode.STARTTLS,
    ssl_context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
) -> SMTPClient:
    return SMTPClient(
        config(mode),
        smtp_factory=lambda host, port, timeout: connection,
        smtp_ssl_factory=lambda host, port, timeout, context: connection,
        ssl_context_factory=ssl_context_factory,
    )


def test_starttls_order_auth_and_verified_context() -> None:
    connection = RecordingSMTP()
    receipt = client(connection).send(envelope())
    assert receipt.accepted is True
    assert connection.events == [
        "ehlo",
        "has_extn:starttls",
        "starttls",
        "ehlo",
        "login",
        "send",
        "quit",
    ]
    assert connection.message is not None
    assert connection.message.get_content_type() == "text/plain"
    assert connection.message["Message-ID"] == "<stage5b@example.test>"


def test_implicit_tls_never_calls_starttls() -> None:
    plain_connection = RecordingSMTP()
    secure_connection = RecordingSMTP()
    factory_events: list[str] = []

    def smtp_factory(host: str, port: int, timeout: float) -> RecordingSMTP:
        factory_events.append("SMTP_FACTORY")
        return plain_connection

    def smtp_ssl_factory(
        host: str, port: int, timeout: float, context: ssl.SSLContext
    ) -> RecordingSMTP:
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        factory_events.append("SMTP_SSL_FACTORY")
        return secure_connection

    transport = SMTPClient(
        config(SMTPSecurityMode.TLS_IMPLICIT),
        smtp_factory=smtp_factory,
        smtp_ssl_factory=smtp_ssl_factory,
    )
    transport.send(envelope())
    assert factory_events == ["SMTP_SSL_FACTORY"]
    assert plain_connection.events == []
    assert secure_connection.events == ["ehlo", "login", "send", "quit"]
    assert secure_connection.events.count("starttls") == 0
    assert secure_connection.events.count("send") == 1


def test_starttls_is_required_and_unverified_context_is_rejected() -> None:
    connection = RecordingSMTP()
    connection.starttls_supported = False
    with pytest.raises(SMTPTLSUnavailableError):
        client(connection).send(envelope())
    assert "send" not in connection.events
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(Exception, match="configuration is invalid"):
        client(RecordingSMTP(), ssl_context_factory=lambda: context).send(envelope())


def test_authentication_failure_is_sanitized_and_closes() -> None:
    connection = RecordingSMTP()
    connection.login_failure = smtplib.SMTPAuthenticationError(535, b"SECRET_PASSWORD")
    with pytest.raises(SMTPAuthenticationFailedError) as error:
        client(connection).send(envelope())
    assert str(error.value) == "SMTP authentication failed."
    assert error.value.classification is SMTPFailureClassification.PERMANENT
    assert connection.events[-1] == "quit"


@pytest.mark.parametrize(
    ("failure", "error_type", "classification"),
    [
        (
            smtplib.SMTPSenderRefused(550, b"secret", "bounce@example.test"),
            SMTPSenderRejectedError,
            SMTPFailureClassification.PERMANENT,
        ),
        (
            smtplib.SMTPRecipientsRefused({"recipient@example.test": (450, b"secret")}),
            SMTPRecipientRejectedError,
            SMTPFailureClassification.TRANSIENT,
        ),
        (
            smtplib.SMTPDataError(554, b"body leaked"),
            SMTPDataRejectedError,
            SMTPFailureClassification.PERMANENT,
        ),
    ],
)
def test_smtp_rejections_are_normalized(
    failure: BaseException,
    error_type: type[Exception],
    classification: SMTPFailureClassification,
) -> None:
    connection = RecordingSMTP()
    connection.send_failure = failure
    with pytest.raises(error_type) as error:
        client(connection).send(envelope())
    assert error.value.classification is classification
    assert "secret" not in str(error.value)
    assert "body" not in str(error.value)


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (smtplib.SMTPServerDisconnected("lost"), SMTPDeliveryOutcomeUnknownError),
        (TimeoutError("secret"), SMTPDeliveryOutcomeUnknownError),
    ],
)
def test_disconnect_or_timeout_during_send_has_unknown_outcome(
    failure: BaseException, error_type: type[Exception]
) -> None:
    connection = RecordingSMTP()
    connection.send_failure = failure
    with pytest.raises(error_type) as error:
        client(connection).send(envelope())
    assert error.value.classification is SMTPFailureClassification.UNKNOWN
    assert connection.events.count("send") == 1


def test_connect_dns_timeout_ssl_and_tls_failures_are_normalized() -> None:
    def failing_factory(error: BaseException):
        def factory(host: str, port: int, timeout: float):
            raise error

        return factory

    cases = [
        (socket.gaierror("secret host"), SMTPConnectionFailedError),
        (ConnectionRefusedError("secret host"), SMTPConnectionFailedError),
        (TimeoutError("secret host"), SMTPTimeoutError),
    ]
    for failure, expected in cases:
        transport = SMTPClient(config(), smtp_factory=failing_factory(failure))
        with pytest.raises(expected) as error:
            transport.send(envelope())
        assert "secret" not in str(error.value)
    connection = RecordingSMTP()

    def tls_failure(*, context: ssl.SSLContext) -> tuple[int, bytes]:
        raise ssl.SSLError("secret certificate")

    connection.starttls = tls_failure  # type: ignore[method-assign]
    with pytest.raises(SMTPTLSNegotiationError):
        client(connection).send(envelope())


def test_cleanup_failure_does_not_replace_primary_failure() -> None:
    connection = RecordingSMTP()
    connection.send_failure = smtplib.SMTPDataError(554, b"primary")

    def broken_quit() -> tuple[int, bytes]:
        raise RuntimeError("secondary")

    connection.quit = broken_quit  # type: ignore[method-assign]
    with pytest.raises(SMTPDataRejectedError):
        client(connection).send(envelope())
    assert connection.events[-1] == "close"

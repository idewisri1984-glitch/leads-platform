from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from app.providers.smtp import (
    FakeSMTPScenario,
    FakeSMTPTransport,
    SMTPAuthenticationFailedError,
    SMTPClient,
    SMTPConnectionFailedError,
    SMTPDataRejectedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPFailureClassification,
    SMTPInternalError,
    SMTPMessageEnvelope,
    SMTPRecipientRejectedError,
    SMTPSecurityMode,
    SMTPSenderIdentity,
    SMTPSenderRejectedError,
    SMTPTimeoutError,
    SMTPTLSNegotiationError,
    SMTPTransportConfig,
)


def config(**changes: object) -> SMTPTransportConfig:
    values: dict[str, object] = {
        "host": "smtp.example.test",
        "port": 587,
        "security_mode": SMTPSecurityMode.STARTTLS,
        "username": "mailer",
        "password": SecretStr("SECRET_PASSWORD"),
        "connection_timeout_seconds": 10.0,
    }
    values.update(changes)
    return SMTPTransportConfig(**values)


def envelope(**changes: object) -> SMTPMessageEnvelope:
    values: dict[str, object] = {
        "envelope_from": "sender@example.test",
        "envelope_to": "recipient@example.test",
        "sender": SMTPSenderIdentity(
            email="sender@example.test",
            display_name="Команда",
            reply_to="reply@example.test",
        ),
        "subject": "Stage 5B test",
        "text_body": "Plain-text integration message. Привет.",
        "message_id": "<stage5b@example.test>",
        "date": datetime(2026, 8, 9, 6, tzinfo=UTC),
    }
    values.update(changes)
    return SMTPMessageEnvelope(**values)


def test_config_is_strict_and_password_is_secret() -> None:
    value = config()
    assert "SECRET_PASSWORD" not in repr(value)
    assert "**********" in repr(value)
    with pytest.raises(ValidationError):
        config(extra="forbidden")
    with pytest.raises(ValidationError):
        config(host=" ")
    with pytest.raises(ValidationError):
        config(port=0)
    with pytest.raises(ValidationError):
        config(connection_timeout_seconds=0.0)
    with pytest.raises(ValidationError):
        config(username=None)


def test_plaintext_is_restricted_to_unauthenticated_loopback() -> None:
    local = config(
        host="127.0.0.1",
        security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
        username=None,
        password=None,
    )
    assert local.host == "127.0.0.1"
    with pytest.raises(ValidationError):
        config(security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost.",
        "smtp.example.com",
        "127.0.0.2",
        "0.0.0.0",
        "192.168.1.10",
        "10.0.0.1",
        "172.16.0.1",
        "8.8.8.8",
        "127.0.0.1\r\n",
        "127.0.0.1\x00",
    ],
)
def test_plaintext_rejects_non_authoritative_loopback_without_connecting(host: str) -> None:
    factory_calls: list[str] = []

    def smtp_factory(host: str, port: int, timeout: float) -> object:
        factory_calls.append(host)
        return object()

    with pytest.raises(ValidationError):
        SMTPClient(
            config(
                host=host,
                security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
                username=None,
                password=None,
            ),
            smtp_factory=smtp_factory,
        )
    assert factory_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("envelope_from", "victim@example.test\r\nBcc: attacker@example.test"),
        ("envelope_to", "victim@example.test\nBcc: attacker@example.test"),
        ("subject", "Hello\nBcc: attacker@example.test"),
        ("message_id", "<safe@example.test>\r\nBcc: attacker@example.test"),
    ],
)
def test_envelope_rejects_header_injection(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        envelope(**{field: value})


def test_addresses_are_ascii_single_recipient_and_body_is_unicode() -> None:
    value = envelope()
    assert value.envelope_to == "recipient@example.test"
    assert "Привет" in value.text_body
    with pytest.raises(ValidationError):
        envelope(envelope_to="first@example.test,second@example.test")
    with pytest.raises(ValidationError):
        envelope(envelope_to="ü@example.test")
    with pytest.raises(ValidationError):
        SMTPSenderIdentity(email="broken", reply_to=None)


@pytest.mark.parametrize(
    "address",
    [
        "a@example.com",
        "first.last@example.co.uk",
        "name+tag@example.com",
        "user_name@example.com",
    ],
)
def test_transport_accepts_supported_ascii_mailboxes(address: str) -> None:
    assert envelope(envelope_to=address).envelope_to == address


@pytest.mark.parametrize(
    "address",
    [
        ".a@example.com",
        "a.@example.com",
        "a..b@example.com",
        "a@example..com",
        "a;@example.com",
        "a@example.com;",
        "a@example",
        "@example.com",
        "a@",
        "a b@example.com",
        "a@example .com",
        "a@example.com,b@example.com",
        "a@example.com;b@example.com",
        "Display Name <a@example.com>",
        "unicode@example.com\N{SNOWMAN}",
        "a@exampl\N{LATIN SMALL LETTER E WITH ACUTE}.com",
        "a@example.com\r",
        "a@example.com\n",
        "a@example.com\x00",
        "a@example.com\x1f",
    ],
)
def test_transport_rejects_unsupported_or_malformed_mailboxes(address: str) -> None:
    with pytest.raises(ValidationError):
        envelope(envelope_to=address)


@pytest.mark.parametrize(
    "message_id",
    ["<a @example.com>", "<@example.com>", "<a@>", "<a@example..com>"],
)
def test_message_id_rejects_malformed_values(message_id: str) -> None:
    with pytest.raises(ValidationError):
        envelope(message_id=message_id)


@pytest.mark.parametrize("control", ["\r", "\n", "\x00"])
@pytest.mark.parametrize("field", ["email", "reply_to", "display_name"])
def test_sender_fields_reject_header_controls(field: str, control: str) -> None:
    values: dict[str, object] = {
        "email": "sender@example.test",
        "reply_to": "reply@example.test",
        "display_name": "Sender",
    }
    values[field] = f"safe{control}injected@example.test"
    with pytest.raises(ValidationError):
        SMTPSenderIdentity(**values)


@pytest.mark.parametrize(
    ("scenario", "error_type"),
    [
        (FakeSMTPScenario.CONNECTION_FAILURE, SMTPConnectionFailedError),
        (FakeSMTPScenario.TLS_FAILURE, SMTPTLSNegotiationError),
        (FakeSMTPScenario.AUTH_FAILURE, SMTPAuthenticationFailedError),
        (FakeSMTPScenario.SENDER_REJECTION, SMTPSenderRejectedError),
        (FakeSMTPScenario.RECIPIENT_REJECTION, SMTPRecipientRejectedError),
        (FakeSMTPScenario.DATA_REJECTION, SMTPDataRejectedError),
        (FakeSMTPScenario.TIMEOUT, SMTPTimeoutError),
        (FakeSMTPScenario.UNKNOWN_OUTCOME, SMTPDeliveryOutcomeUnknownError),
        (FakeSMTPScenario.INTERNAL_FAILURE, SMTPInternalError),
    ],
)
def test_fake_transport_has_deterministic_failures(
    scenario: FakeSMTPScenario, error_type: type[Exception]
) -> None:
    transport = FakeSMTPTransport(scenario=scenario)
    with pytest.raises(error_type):
        transport.send(envelope())
    assert transport.calls == [envelope()]


def test_fake_transport_success_and_classification() -> None:
    transport = FakeSMTPTransport()
    receipt = transport.send(envelope())
    assert receipt.accepted is True
    assert receipt.message_id == "<stage5b@example.test>"
    transient = FakeSMTPTransport(scenario=FakeSMTPScenario.RECIPIENT_REJECTION, rejection_code=450)
    with pytest.raises(SMTPRecipientRejectedError) as error:
        transient.send(envelope())
    assert error.value.classification is SMTPFailureClassification.TRANSIENT

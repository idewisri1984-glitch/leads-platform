from __future__ import annotations

import smtplib
import socket
import ssl
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Protocol, cast

from pydantic import ValidationError

from .contracts import (
    SMTPDeliveryReceipt,
    SMTPMessageEnvelope,
    SMTPSecurityMode,
    SMTPTransportConfig,
)
from .errors import (
    SMTPAuthenticationFailedError,
    SMTPConfigurationError,
    SMTPConnectionFailedError,
    SMTPDataRejectedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPInternalError,
    SMTPProtocolError,
    SMTPRecipientRejectedError,
    SMTPSenderRejectedError,
    SMTPTimeoutError,
    SMTPTLSNegotiationError,
    SMTPTLSUnavailableError,
    SMTPTransportError,
    classify_smtp_code,
)
from .interfaces import SMTPTransport


class _SMTPConnection(Protocol):
    def ehlo(self) -> tuple[int, bytes]: ...

    def has_extn(self, name: str) -> bool: ...

    def starttls(self, *, context: ssl.SSLContext) -> tuple[int, bytes]: ...

    def login(self, user: str, password: str) -> tuple[int, bytes]: ...

    def send_message(
        self, msg: EmailMessage, from_addr: str, to_addrs: list[str]
    ) -> dict[str, tuple[int, bytes]]: ...

    def quit(self) -> tuple[int, bytes]: ...

    def close(self) -> None: ...


SMTPFactory = Callable[[str, int, float], _SMTPConnection]
SMTPSSLFactory = Callable[[str, int, float, ssl.SSLContext], _SMTPConnection]


def _smtp_factory(host: str, port: int, timeout: float) -> _SMTPConnection:
    return cast(_SMTPConnection, smtplib.SMTP(host=host, port=port, timeout=timeout))


def _smtp_ssl_factory(
    host: str, port: int, timeout: float, context: ssl.SSLContext
) -> _SMTPConnection:
    return cast(
        _SMTPConnection,
        smtplib.SMTP_SSL(host=host, port=port, timeout=timeout, context=context),
    )


class SMTPClient(SMTPTransport):
    def __init__(
        self,
        config: SMTPTransportConfig,
        *,
        smtp_factory: SMTPFactory = _smtp_factory,
        smtp_ssl_factory: SMTPSSLFactory = _smtp_ssl_factory,
        ssl_context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
        clock: Callable[[], datetime] | None = None,
        message_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if type(config) is not SMTPTransportConfig:
            raise SMTPConfigurationError()
        try:
            self.config = SMTPTransportConfig(**config.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise SMTPConfigurationError() from None
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory
        self._ssl_context_factory = ssl_context_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._message_id_factory = message_id_factory or (lambda: make_msgid(domain="localhost"))

    def send(self, message: SMTPMessageEnvelope) -> SMTPDeliveryReceipt:
        if type(message) is not SMTPMessageEnvelope:
            raise SMTPConfigurationError()
        try:
            envelope = SMTPMessageEnvelope(**message.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise SMTPConfigurationError() from None
        email_message, message_id = self._build_message(envelope)
        connection: _SMTPConnection | None = None
        sending = False
        try:
            connection = self._connect()
            self._secure_and_authenticate(connection)
            sending = True
            refused = connection.send_message(
                email_message,
                from_addr=envelope.envelope_from,
                to_addrs=[envelope.envelope_to],
            )
            if refused:
                code = next(iter(refused.values()))[0]
                raise SMTPRecipientRejectedError(
                    smtp_code=code, classification=classify_smtp_code(code)
                )
            return SMTPDeliveryReceipt(
                accepted=True,
                recipient=envelope.envelope_to,
                message_id=message_id,
                smtp_code=None,
                provider="stdlib-smtp",
                security_mode=self.config.security_mode,
            )
        except SMTPTransportError:
            raise
        except smtplib.SMTPAuthenticationError:
            raise SMTPAuthenticationFailedError() from None
        except smtplib.SMTPSenderRefused as error:
            raise SMTPSenderRejectedError(
                smtp_code=error.smtp_code,
                classification=classify_smtp_code(error.smtp_code),
            ) from None
        except smtplib.SMTPRecipientsRefused as error:
            recipient_code = self._recipient_code(error.recipients)
            raise SMTPRecipientRejectedError(
                smtp_code=recipient_code,
                classification=classify_smtp_code(recipient_code),
            ) from None
        except smtplib.SMTPDataError as error:
            raise SMTPDataRejectedError(
                smtp_code=error.smtp_code,
                classification=classify_smtp_code(error.smtp_code),
            ) from None
        except smtplib.SMTPServerDisconnected:
            if sending:
                raise SMTPDeliveryOutcomeUnknownError() from None
            raise SMTPConnectionFailedError() from None
        except TimeoutError:
            if sending:
                raise SMTPDeliveryOutcomeUnknownError() from None
            raise SMTPTimeoutError() from None
        except ssl.SSLError:
            raise SMTPTLSNegotiationError() from None
        except (socket.gaierror, ConnectionRefusedError, OSError):
            raise SMTPConnectionFailedError() from None
        except smtplib.SMTPResponseException as error:
            raise SMTPProtocolError(
                smtp_code=error.smtp_code,
                classification=classify_smtp_code(error.smtp_code),
            ) from None
        except smtplib.SMTPException:
            raise SMTPProtocolError() from None
        except Exception:
            raise SMTPInternalError() from None
        finally:
            self._cleanup(connection)

    def _connect(self) -> _SMTPConnection:
        config = self.config
        if config.security_mode is SMTPSecurityMode.TLS_IMPLICIT:
            return self._smtp_ssl_factory(
                config.host,
                config.port,
                config.connection_timeout_seconds,
                self._verified_context(),
            )
        return self._smtp_factory(config.host, config.port, config.connection_timeout_seconds)

    def _secure_and_authenticate(self, connection: _SMTPConnection) -> None:
        self._ehlo(connection)
        if self.config.security_mode is SMTPSecurityMode.STARTTLS:
            if not connection.has_extn("starttls"):
                raise SMTPTLSUnavailableError()
            try:
                connection.starttls(context=self._verified_context())
            except (ssl.SSLError, smtplib.SMTPException, OSError):
                raise SMTPTLSNegotiationError() from None
            self._ehlo(connection)
        if self.config.username is not None:
            password = self.config.password
            if password is None:
                raise SMTPConfigurationError()
            connection.login(self.config.username, password.get_secret_value())

    @staticmethod
    def _ehlo(connection: _SMTPConnection) -> None:
        code, _ = connection.ehlo()
        if not 200 <= code < 400:
            raise SMTPProtocolError(smtp_code=code, classification=classify_smtp_code(code))

    def _verified_context(self) -> ssl.SSLContext:
        context = self._ssl_context_factory()
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise SMTPConfigurationError()
        return context

    def _build_message(self, envelope: SMTPMessageEnvelope) -> tuple[EmailMessage, str]:
        message_id = envelope.message_id or self._message_id_factory()
        try:
            validated_message_id = SMTPMessageEnvelope.validate_message_id(message_id)
        except ValueError:
            raise SMTPConfigurationError() from None
        if validated_message_id is None:
            raise SMTPConfigurationError()
        date = envelope.date or self._clock()
        if date.tzinfo is None or date.utcoffset() is None:
            raise SMTPConfigurationError()
        message = EmailMessage()
        sender = envelope.sender
        message["From"] = str(
            Address(display_name=sender.display_name or "", addr_spec=sender.email)
        )
        message["To"] = envelope.envelope_to
        if sender.reply_to is not None:
            message["Reply-To"] = sender.reply_to
        message["Subject"] = envelope.subject
        message["Message-ID"] = validated_message_id
        message["Date"] = format_datetime(date)
        message.set_content(envelope.text_body, subtype="plain", charset="utf-8")
        return message, message_id

    @staticmethod
    def _recipient_code(recipients: dict[str, tuple[int, bytes]]) -> int | None:
        return next(iter(recipients.values()))[0] if recipients else None

    @staticmethod
    def _cleanup(connection: _SMTPConnection | None) -> None:
        if connection is None:
            return
        try:
            connection.quit()
        except Exception:
            with suppress(Exception):
                connection.close()

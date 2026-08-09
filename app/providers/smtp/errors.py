from enum import StrEnum


class SMTPFailureClassification(StrEnum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


def classify_smtp_code(code: int | None) -> SMTPFailureClassification:
    if code is None:
        return SMTPFailureClassification.UNKNOWN
    if 400 <= code < 500:
        return SMTPFailureClassification.TRANSIENT
    if 500 <= code < 600:
        return SMTPFailureClassification.PERMANENT
    return SMTPFailureClassification.UNKNOWN


class SMTPTransportError(Exception):
    message = "SMTP transport failed."
    default_classification = SMTPFailureClassification.UNKNOWN

    def __init__(
        self,
        *,
        smtp_code: int | None = None,
        classification: SMTPFailureClassification | None = None,
    ) -> None:
        super().__init__(self.message)
        self.smtp_code = smtp_code
        self.classification = classification or self.default_classification


class SMTPConfigurationError(SMTPTransportError):
    message = "SMTP configuration is invalid."
    default_classification = SMTPFailureClassification.PERMANENT


class SMTPConnectionFailedError(SMTPTransportError):
    message = "SMTP connection failed."
    default_classification = SMTPFailureClassification.TRANSIENT


class SMTPTLSUnavailableError(SMTPTransportError):
    message = "SMTP STARTTLS is unavailable."
    default_classification = SMTPFailureClassification.PERMANENT


class SMTPTLSNegotiationError(SMTPTransportError):
    message = "SMTP TLS negotiation failed."


class SMTPAuthenticationFailedError(SMTPTransportError):
    message = "SMTP authentication failed."
    default_classification = SMTPFailureClassification.PERMANENT


class SMTPSenderRejectedError(SMTPTransportError):
    message = "SMTP sender was rejected."


class SMTPRecipientRejectedError(SMTPTransportError):
    message = "SMTP recipient was rejected."


class SMTPDataRejectedError(SMTPTransportError):
    message = "SMTP message data was rejected."


class SMTPProtocolError(SMTPTransportError):
    message = "SMTP protocol failed."


class SMTPTimeoutError(SMTPTransportError):
    message = "SMTP operation timed out."
    default_classification = SMTPFailureClassification.TRANSIENT


class SMTPInternalError(SMTPTransportError):
    message = "SMTP transport failed."


class SMTPDeliveryOutcomeUnknownError(SMTPTransportError):
    message = "SMTP delivery outcome is unknown."
    default_classification = SMTPFailureClassification.UNKNOWN

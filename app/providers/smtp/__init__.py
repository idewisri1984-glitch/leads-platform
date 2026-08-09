from .client import SMTPClient
from .contracts import (
    SMTPDeliveryReceipt,
    SMTPMessageEnvelope,
    SMTPSecurityMode,
    SMTPSenderIdentity,
    SMTPTransportConfig,
)
from .errors import (
    SMTPAuthenticationFailedError,
    SMTPConfigurationError,
    SMTPConnectionFailedError,
    SMTPDataRejectedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPFailureClassification,
    SMTPInternalError,
    SMTPProtocolError,
    SMTPRecipientRejectedError,
    SMTPSenderRejectedError,
    SMTPTimeoutError,
    SMTPTLSNegotiationError,
    SMTPTLSUnavailableError,
    SMTPTransportError,
)
from .fake import FakeSMTPScenario, FakeSMTPTransport
from .interfaces import SMTPTransport

__all__ = [
    "FakeSMTPScenario",
    "FakeSMTPTransport",
    "SMTPAuthenticationFailedError",
    "SMTPClient",
    "SMTPConfigurationError",
    "SMTPConnectionFailedError",
    "SMTPDataRejectedError",
    "SMTPDeliveryOutcomeUnknownError",
    "SMTPDeliveryReceipt",
    "SMTPFailureClassification",
    "SMTPInternalError",
    "SMTPMessageEnvelope",
    "SMTPProtocolError",
    "SMTPRecipientRejectedError",
    "SMTPSecurityMode",
    "SMTPSenderIdentity",
    "SMTPSenderRejectedError",
    "SMTPTLSNegotiationError",
    "SMTPTLSUnavailableError",
    "SMTPTimeoutError",
    "SMTPTransport",
    "SMTPTransportConfig",
    "SMTPTransportError",
]

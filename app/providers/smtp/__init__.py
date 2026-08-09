from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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

_EXPORT_MODULES = {
    "FakeSMTPScenario": ".fake",
    "FakeSMTPTransport": ".fake",
    "SMTPAuthenticationFailedError": ".errors",
    "SMTPClient": ".client",
    "SMTPConfigurationError": ".errors",
    "SMTPConnectionFailedError": ".errors",
    "SMTPDataRejectedError": ".errors",
    "SMTPDeliveryOutcomeUnknownError": ".errors",
    "SMTPDeliveryReceipt": ".contracts",
    "SMTPFailureClassification": ".errors",
    "SMTPInternalError": ".errors",
    "SMTPMessageEnvelope": ".contracts",
    "SMTPProtocolError": ".errors",
    "SMTPRecipientRejectedError": ".errors",
    "SMTPSecurityMode": ".contracts",
    "SMTPSenderIdentity": ".contracts",
    "SMTPSenderRejectedError": ".errors",
    "SMTPTLSNegotiationError": ".errors",
    "SMTPTLSUnavailableError": ".errors",
    "SMTPTimeoutError": ".errors",
    "SMTPTransport": ".interfaces",
    "SMTPTransportConfig": ".contracts",
    "SMTPTransportError": ".errors",
}

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


def __getattr__(name: str) -> object:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value

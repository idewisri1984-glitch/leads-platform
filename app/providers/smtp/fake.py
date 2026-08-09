from dataclasses import dataclass, field
from enum import StrEnum

from .contracts import SMTPDeliveryReceipt, SMTPMessageEnvelope, SMTPSecurityMode
from .errors import (
    SMTPAuthenticationFailedError,
    SMTPConnectionFailedError,
    SMTPDataRejectedError,
    SMTPDeliveryOutcomeUnknownError,
    SMTPInternalError,
    SMTPRecipientRejectedError,
    SMTPSenderRejectedError,
    SMTPTimeoutError,
    SMTPTLSNegotiationError,
    classify_smtp_code,
)
from .interfaces import SMTPTransport


class FakeSMTPScenario(StrEnum):
    SUCCESS = "SUCCESS"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    TLS_FAILURE = "TLS_FAILURE"
    AUTH_FAILURE = "AUTH_FAILURE"
    SENDER_REJECTION = "SENDER_REJECTION"
    RECIPIENT_REJECTION = "RECIPIENT_REJECTION"
    DATA_REJECTION = "DATA_REJECTION"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


@dataclass(slots=True)
class FakeSMTPTransport(SMTPTransport):
    scenario: FakeSMTPScenario = FakeSMTPScenario.SUCCESS
    rejection_code: int = 550
    calls: list[SMTPMessageEnvelope] = field(default_factory=list)

    def send(self, message: SMTPMessageEnvelope) -> SMTPDeliveryReceipt:
        validated = SMTPMessageEnvelope(**message.model_dump())
        self.calls.append(validated)
        if self.scenario is FakeSMTPScenario.CONNECTION_FAILURE:
            raise SMTPConnectionFailedError()
        if self.scenario is FakeSMTPScenario.TLS_FAILURE:
            raise SMTPTLSNegotiationError()
        if self.scenario is FakeSMTPScenario.AUTH_FAILURE:
            raise SMTPAuthenticationFailedError()
        classification = classify_smtp_code(self.rejection_code)
        if self.scenario is FakeSMTPScenario.SENDER_REJECTION:
            raise SMTPSenderRejectedError(
                smtp_code=self.rejection_code, classification=classification
            )
        if self.scenario is FakeSMTPScenario.RECIPIENT_REJECTION:
            raise SMTPRecipientRejectedError(
                smtp_code=self.rejection_code, classification=classification
            )
        if self.scenario is FakeSMTPScenario.DATA_REJECTION:
            raise SMTPDataRejectedError(
                smtp_code=self.rejection_code, classification=classification
            )
        if self.scenario is FakeSMTPScenario.TIMEOUT:
            raise SMTPTimeoutError()
        if self.scenario is FakeSMTPScenario.UNKNOWN_OUTCOME:
            raise SMTPDeliveryOutcomeUnknownError()
        if self.scenario is FakeSMTPScenario.INTERNAL_FAILURE:
            raise SMTPInternalError()
        return SMTPDeliveryReceipt(
            accepted=True,
            recipient=validated.envelope_to,
            message_id=validated.message_id or "<fake-smtp@example.test>",
            smtp_code=250,
            provider="fake-smtp",
            security_mode=SMTPSecurityMode.STARTTLS,
        )

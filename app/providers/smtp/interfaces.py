from typing import Protocol

from .contracts import SMTPDeliveryReceipt, SMTPMessageEnvelope


class SMTPTransport(Protocol):
    def send(self, message: SMTPMessageEnvelope) -> SMTPDeliveryReceipt: ...

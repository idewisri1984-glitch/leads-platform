from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.email_draft.models import EmailDraft


class EmailDeliveryMode(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"


def claim_email_delivery_mode(
    session: Session,
    *,
    email_draft_id: int,
    mode: EmailDeliveryMode,
) -> bool:
    result = session.connection().execute(
        update(EmailDraft)
        .where(EmailDraft.id == email_draft_id, EmailDraft.delivery_mode.is_(None))
        .values(delivery_mode=mode.value)
    )
    return result.rowcount == 1

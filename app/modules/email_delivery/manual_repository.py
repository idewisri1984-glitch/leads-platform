from sqlalchemy import select
from sqlalchemy.orm import Session

from .manual_models import ManualEmailSendRecord
from .manual_schemas import ManualEmailSendRecordCreate


class ManualEmailSendRecordRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_email_draft_id(self, email_draft_id: int) -> ManualEmailSendRecord | None:
        return self.session.scalar(
            select(ManualEmailSendRecord).where(
                ManualEmailSendRecord.email_draft_id == email_draft_id
            )
        )

    def create(self, data: ManualEmailSendRecordCreate) -> ManualEmailSendRecord:
        record = ManualEmailSendRecord(**data.model_dump())
        self.session.add(record)
        self.session.flush()
        return record

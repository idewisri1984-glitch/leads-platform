from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ManualOutreachStatus(StrEnum):
    READY_FOR_MANUAL_SEND = "READY_FOR_MANUAL_SEND"
    MANUALLY_SENT = "MANUALLY_SENT"


class ManualRecipientType(StrEnum):
    PERSON = "PERSON"
    COMPANY = "COMPANY"


class ManualEmailDraftScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: int = Field(gt=0)
    company_id: int = Field(gt=0)
    contact_id: int = Field(gt=0)
    email_draft_id: int = Field(gt=0)


class ConfirmedManualEmailSendCommand(ManualEmailDraftScope):
    confirmed: bool


class ExternalManualEmailDraftScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: int = Field(gt=0)
    company_id: int = Field(gt=0)
    email_draft_id: int = Field(gt=0)


class ConfirmedExternalManualEmailSendCommand(ExternalManualEmailDraftScope):
    confirmed: bool


class ManualEmailCopyPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: int
    company_id: int
    contact_id: int | None
    lead_id: int
    task_id: int
    email_draft_id: int
    recipient_email: str
    recipient_name: str
    company_name: str
    subject: str
    text_body: str
    draft_status: str
    recipient_type: ManualRecipientType
    outreach_status: ManualOutreachStatus
    content_hash: str
    manual_send_record_id: int | None = None
    sent_at: datetime | None = None


class ManualEmailSendRecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: int = Field(gt=0)
    company_id: int = Field(gt=0)
    contact_id: int | None = Field(default=None, gt=0)
    email_draft_id: int = Field(gt=0)
    recipient_email: str
    sent_at: datetime

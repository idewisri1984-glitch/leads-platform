from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EmailDraft, EmailDraftStatus


class EmailDraftRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_scope(
        self, *, project_id: int, company_id: int, contact_id: int, draft_id: int
    ) -> EmailDraft | None:
        return self.session.scalar(
            select(EmailDraft).where(
                EmailDraft.id == draft_id,
                EmailDraft.project_id == project_id,
                EmailDraft.company_id == company_id,
                EmailDraft.contact_id == contact_id,
            )
        )

    def get_for_scope_for_update(
        self, *, project_id: int, company_id: int, contact_id: int, draft_id: int
    ) -> EmailDraft | None:
        return self.session.scalar(
            select(EmailDraft)
            .where(
                EmailDraft.id == draft_id,
                EmailDraft.project_id == project_id,
                EmailDraft.company_id == company_id,
                EmailDraft.contact_id == contact_id,
            )
            .with_for_update()
        )

    def find_by_request_fingerprint(self, fingerprint: str) -> EmailDraft | None:
        return self.session.scalar(
            select(EmailDraft).where(EmailDraft.request_fingerprint == fingerprint)
        )

    def add(self, draft: EmailDraft) -> EmailDraft:
        self.session.add(draft)
        self.session.flush()
        return draft

    def list_for_task(self, task_id: int) -> list[EmailDraft]:
        return list(
            self.session.scalars(
                select(EmailDraft).where(EmailDraft.task_id == task_id).order_by(EmailDraft.id)
            )
        )

    @staticmethod
    def is_reusable(draft: EmailDraft) -> bool:
        return draft.status == EmailDraftStatus.DRAFT.value

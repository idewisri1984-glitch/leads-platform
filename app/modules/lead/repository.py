from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.lead.models import Lead


class LeadRepository:
    """
    Repository for Lead entity.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        company_id: int,
        contact_id: int | None = None,
        status: str | None = None,
        source: str | None = None,
        notes: str | None = None,
    ) -> Lead:
        lead = Lead(
            company_id=company_id,
            contact_id=contact_id,
            status=status,
            source=source,
            notes=notes,
        )

        self.session.add(lead)
        self.session.commit()
        self.session.refresh(lead)

        return lead

    def get(self, lead_id: int) -> Lead | None:
        statement = select(Lead).where(Lead.id == lead_id)
        return self.session.scalar(statement)

    def get_all(self) -> list[Lead]:
        statement = select(Lead).order_by(Lead.id)
        return list(self.session.scalars(statement))

    def get_by_company(self, company_id: int) -> list[Lead]:
        statement = select(Lead).where(Lead.company_id == company_id).order_by(Lead.id)

        return list(self.session.scalars(statement))

    def get_by_contact(self, contact_id: int) -> list[Lead]:
        statement = select(Lead).where(Lead.contact_id == contact_id).order_by(Lead.id)

        return list(self.session.scalars(statement))

    def update(self, lead: Lead) -> Lead:
        self.session.add(lead)
        self.session.commit()
        self.session.refresh(lead)

        return lead

    def delete(self, lead: Lead) -> None:
        self.session.delete(lead)
        self.session.commit()

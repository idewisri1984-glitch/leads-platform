from app.modules.contact.repository import ContactRepository
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.lead.schemas import LeadCreate, LeadRead


class LeadService:
    """
    Lead business logic.
    """

    def __init__(
        self,
        repository: LeadRepository,
        contact_repository: ContactRepository,
    ) -> None:
        self.repository = repository
        self.contact_repository = contact_repository

    def create(self, data: LeadCreate) -> LeadRead:
        if data.contact_id is not None:
            contact = self.contact_repository.get(data.contact_id)

            if contact is None or contact.company_id != data.company_id:
                raise ValueError(
                    f"Contact {data.contact_id} does not belong to company {data.company_id}."
                )

        lead = self.repository.create(
            company_id=data.company_id,
            contact_id=data.contact_id,
            status=data.status,
            source=data.source,
            notes=data.notes,
        )

        return LeadRead.model_validate(lead)

    def get(self, lead_id: int) -> LeadRead | None:
        lead = self.repository.get(lead_id)

        if lead is None:
            return None

        return LeadRead.model_validate(lead)

    def get_all(self) -> list[LeadRead]:
        leads = self.repository.get_all()

        return [LeadRead.model_validate(lead) for lead in leads]

    def get_by_company(self, company_id: int) -> list[LeadRead]:
        leads = self.repository.get_by_company(company_id)

        return [LeadRead.model_validate(lead) for lead in leads]

    def get_by_contact(self, contact_id: int) -> list[LeadRead]:
        leads = self.repository.get_by_contact(contact_id)

        return [LeadRead.model_validate(lead) for lead in leads]

    def update(self, lead: Lead) -> LeadRead:
        lead = self.repository.update(lead)

        return LeadRead.model_validate(lead)

    def delete(self, lead: Lead) -> None:
        self.repository.delete(lead)

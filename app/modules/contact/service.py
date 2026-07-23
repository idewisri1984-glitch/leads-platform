from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.contact.schemas import ContactCreate, ContactRead


class ContactService:
    """
    Contact business logic.
    """

    def __init__(self, repository: ContactRepository) -> None:
        self.repository = repository

    def create(self, data: ContactCreate) -> ContactRead:
        contact = self.repository.create(
            company_id=data.company_id,
            first_name=data.first_name,
            last_name=data.last_name,
            job_title=data.job_title,
            email=data.email,
            phone=data.phone,
            linkedin_url=data.linkedin_url,
            instagram_url=data.instagram_url,
            country=data.country,
            city=data.city,
            source=data.source,
            external_id=data.external_id,
            status=data.status,
            notes=data.notes,
        )

        return ContactRead.model_validate(contact)

    def get(self, contact_id: int) -> ContactRead | None:
        contact = self.repository.get(contact_id)

        if contact is None:
            return None

        return ContactRead.model_validate(contact)

    def get_all(self) -> list[ContactRead]:
        contacts = self.repository.get_all()

        return [ContactRead.model_validate(contact) for contact in contacts]

    def get_by_company(self, company_id: int) -> list[ContactRead]:
        contacts = self.repository.get_by_company(company_id)

        return [ContactRead.model_validate(contact) for contact in contacts]

    def update(self, contact: Contact) -> ContactRead:
        contact = self.repository.update(contact)

        return ContactRead.model_validate(contact)

    def delete(self, contact: Contact) -> None:
        self.repository.delete(contact)

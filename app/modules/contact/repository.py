from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.contact.models import Contact


class ContactRepository:
    """
    Repository for Contact entity.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        company_id: int,
        first_name: str,
        last_name: str | None = None,
        job_title: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        linkedin_url: str | None = None,
        country: str | None = None,
        city: str | None = None,
        source: str | None = None,
        external_id: str | None = None,
        status: str | None = None,
        notes: str | None = None,
    ) -> Contact:
        contact = Contact(
            company_id=company_id,
            first_name=first_name,
            last_name=last_name,
            job_title=job_title,
            email=email,
            phone=phone,
            linkedin_url=linkedin_url,
            country=country,
            city=city,
            source=source,
            external_id=external_id,
            status=status,
            notes=notes,
        )

        self.session.add(contact)
        self.session.commit()
        self.session.refresh(contact)

        return contact

    def get(self, contact_id: int) -> Contact | None:
        statement = select(Contact).where(Contact.id == contact_id)
        return self.session.scalar(statement)

    def get_all(self) -> list[Contact]:
        statement = select(Contact).order_by(Contact.id)
        return list(self.session.scalars(statement))

    def get_by_company(self, company_id: int) -> list[Contact]:
        statement = select(Contact).where(Contact.company_id == company_id).order_by(Contact.id)

        return list(self.session.scalars(statement))

    def update(self, contact: Contact) -> Contact:
        self.session.add(contact)
        self.session.commit()
        self.session.refresh(contact)

        return contact

    def delete(self, contact: Contact) -> None:
        self.session.delete(contact)
        self.session.commit()

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.contact.channel_normalization import (
    normalize_contact_email,
    normalize_contact_phone,
    normalize_instagram_url,
    normalize_linkedin_url,
)
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
        first_name: str | None,
        last_name: str | None = None,
        job_title: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        linkedin_url: str | None = None,
        instagram_url: str | None = None,
        country: str | None = None,
        city: str | None = None,
        source: str | None = None,
        external_id: str | None = None,
        status: str | None = None,
        notes: str | None = None,
    ) -> Contact:
        first_name = self._normalize_name(first_name, "First name")
        last_name = self._normalize_name(last_name, "Last name")
        email = normalize_contact_email(email)
        phone = normalize_contact_phone(phone)
        linkedin_url = self._bounded_social_url(normalize_linkedin_url(linkedin_url), "LinkedIn")
        instagram_url = self._bounded_social_url(
            normalize_instagram_url(instagram_url), "Instagram"
        )
        if not any(
            value is not None
            for value in (
                first_name,
                last_name,
                email,
                phone,
                linkedin_url,
                instagram_url,
            )
        ):
            raise ValueError("A contact requires a name or usable contact channel.")

        contact = Contact(
            company_id=company_id,
            first_name=first_name,
            last_name=last_name,
            job_title=job_title,
            email=email,
            phone=phone,
            linkedin_url=linkedin_url,
            instagram_url=instagram_url,
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

    @staticmethod
    def _normalize_name(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if len(normalized) > 100:
            raise ValueError(f"{label} exceeds 100 characters.")
        return normalized

    @staticmethod
    def _bounded_social_url(value: str | None, platform: str) -> str | None:
        if value is not None and len(value) > 255:
            raise ValueError(f"{platform} URL exceeds 255 characters.")
        return value

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

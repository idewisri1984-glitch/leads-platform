from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.contact.models import Contact

_INVALID_PROMOTION_DATA = "Contact promotion data is invalid."


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

    def acquire_promotion_scope(self, company_id: int) -> None:
        self._validate_positive_id(company_id)
        if self.session.get_bind().dialect.name == "sqlite":
            self.session.execute(
                update(Company).where(Company.id == company_id).values(id=Company.id)
            )
            company_exists = self.session.scalar(select(Company.id).where(Company.id == company_id))
        else:
            company_exists = self.session.scalar(
                select(Company.id).where(Company.id == company_id).with_for_update()
            )
        if company_exists is None:
            raise ValueError("Company was not found.")

    def get_for_company(self, company_id: int, contact_id: int) -> Contact | None:
        self._validate_positive_id(company_id)
        self._validate_positive_id(contact_id)
        return self.session.scalar(
            select(Contact).where(
                Contact.company_id == company_id,
                Contact.id == contact_id,
            )
        )

    def find_promotion_duplicate_by_email(
        self,
        company_id: int,
        normalized_email: str,
    ) -> Contact | None:
        self._validate_positive_id(company_id)
        if (
            not isinstance(normalized_email, str)
            or not normalized_email
            or len(normalized_email) > 255
            or normalized_email != normalized_email.strip().casefold()
        ):
            raise ValueError("Normalized promotion email is invalid.")
        return self.session.scalar(
            select(Contact)
            .where(
                Contact.company_id == company_id,
                Contact.email.is_not(None),
                func.lower(func.trim(Contact.email)) == normalized_email,
            )
            .order_by(Contact.id)
            .limit(1)
        )

    def create_for_promotion(
        self,
        *,
        company_id: int,
        first_name: str,
        last_name: str | None,
        job_title: str | None,
        email: str | None,
        phone: str | None,
        source: str,
        external_id: str | None,
        status: str = "NEW",
    ) -> Contact:
        self._validate_positive_id(company_id)
        values = (
            (first_name, 100, True),
            (last_name, 100, False),
            (job_title, 150, False),
            (email, 255, False),
            (phone, 100, False),
            (source, 100, True),
            (external_id, 255, False),
            (status, 50, True),
        )
        if any(
            not self._valid_promotion_text(value, maximum, required)
            for value, maximum, required in values
        ):
            raise ValueError(_INVALID_PROMOTION_DATA)
        if self.session.scalar(select(Company.id).where(Company.id == company_id)) is None:
            raise ValueError("Company was not found.")
        contact = Contact(
            company_id=company_id,
            first_name=first_name,
            last_name=last_name,
            job_title=job_title,
            email=email,
            phone=phone,
            source=source,
            external_id=external_id,
            status=status,
        )
        self.session.add(contact)
        self.session.flush()
        return contact

    def update(self, contact: Contact) -> Contact:
        self.session.add(contact)
        self.session.commit()
        self.session.refresh(contact)

        return contact

    def delete(self, contact: Contact) -> None:
        self.session.delete(contact)
        self.session.commit()

    @staticmethod
    def _validate_positive_id(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(_INVALID_PROMOTION_DATA)

    @staticmethod
    def _valid_promotion_text(
        value: str | None,
        maximum: int,
        required: bool,
    ) -> bool:
        if value is None:
            return not required
        if not isinstance(value, str) or len(value) > maximum:
            return False
        return not required or bool(value.strip())

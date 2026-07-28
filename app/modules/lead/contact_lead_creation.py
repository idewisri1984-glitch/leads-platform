from typing import Protocol

from app.modules.lead.contact_lead_creation_schemas import ContactLeadCreationResult

_INVALID_DATA = "Lead creation data is invalid."
_NOT_FOUND = "Contact was not found."
_INCONSISTENT_STATE = "Lead creation state is inconsistent."


class ContactLeadCreationError(ValueError):
    pass


class ContactLeadCreationNotFoundError(ContactLeadCreationError):
    pass


class ContactLeadCreationInvalidDataError(ContactLeadCreationError):
    pass


class ContactLeadCreationConsistencyError(ContactLeadCreationError):
    pass


class ContactLeadCreationContactRecord(Protocol):
    id: int
    company_id: int


class ContactLeadCreationLeadRecord(Protocol):
    id: int
    company_id: int
    contact_id: int | None
    status: str
    source: str | None
    notes: str | None


class ContactLeadCreationContactRepository(Protocol):
    def get_for_company(
        self,
        company_id: int,
        contact_id: int,
    ) -> ContactLeadCreationContactRecord | None: ...


class ContactLeadCreationLeadRepository(Protocol):
    def create_for_contact(
        self,
        *,
        company_id: int,
        contact_id: int,
        status: str = "NEW",
        source: str | None = None,
    ) -> ContactLeadCreationLeadRecord: ...


class ContactLeadCreationService:
    def __init__(
        self,
        contact_repository: ContactLeadCreationContactRepository,
        lead_repository: ContactLeadCreationLeadRepository,
    ) -> None:
        self.contact_repository = contact_repository
        self.lead_repository = lead_repository

    def create(
        self,
        company_id: int,
        contact_id: int,
    ) -> ContactLeadCreationResult:
        self._validate_id(company_id)
        self._validate_id(contact_id)

        contact: ContactLeadCreationContactRecord | None = None
        contact_error: ContactLeadCreationConsistencyError | None = None
        try:
            contact = self.contact_repository.get_for_company(company_id, contact_id)
        except (TypeError, ValueError):
            contact_error = ContactLeadCreationConsistencyError(_INCONSISTENT_STATE)
        if contact_error is not None:
            raise contact_error from None
        if contact is None:
            raise ContactLeadCreationNotFoundError(_NOT_FOUND)
        self._validate_contact(contact, company_id, contact_id)
        lead: ContactLeadCreationLeadRecord | None = None
        lead_error: ContactLeadCreationConsistencyError | None = None
        try:
            lead = self.lead_repository.create_for_contact(
                company_id=company_id,
                contact_id=contact_id,
                status="NEW",
                source=None,
            )
        except (TypeError, ValueError):
            lead_error = ContactLeadCreationConsistencyError(_INCONSISTENT_STATE)
        if lead_error is not None:
            raise lead_error from None
        if lead is None:
            raise ContactLeadCreationConsistencyError(_INCONSISTENT_STATE)
        self._validate_lead(lead, company_id, contact_id)

        return ContactLeadCreationResult(
            lead_id=lead.id,
            company_id=company_id,
            contact_id=contact_id,
            status="NEW",
        )

    @staticmethod
    def _validate_id(value: object) -> None:
        if not ContactLeadCreationService._is_positive_int(value):
            raise ContactLeadCreationInvalidDataError(_INVALID_DATA)

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return type(value) is int and value > 0

    @staticmethod
    def _validate_contact(
        contact: ContactLeadCreationContactRecord,
        company_id: int,
        contact_id: int,
    ) -> None:
        returned_contact_id = getattr(contact, "id", None)
        returned_company_id = getattr(contact, "company_id", None)
        if (
            not ContactLeadCreationService._is_positive_int(returned_contact_id)
            or not ContactLeadCreationService._is_positive_int(returned_company_id)
            or returned_contact_id != contact_id
            or returned_company_id != company_id
        ):
            raise ContactLeadCreationConsistencyError(_INCONSISTENT_STATE)

    @staticmethod
    def _validate_lead(
        lead: ContactLeadCreationLeadRecord,
        company_id: int,
        contact_id: int,
    ) -> None:
        lead_id = getattr(lead, "id", None)
        returned_company_id = getattr(lead, "company_id", None)
        returned_contact_id = getattr(lead, "contact_id", None)
        status = getattr(lead, "status", None)
        source = getattr(lead, "source", None)
        notes = getattr(lead, "notes", None)
        if (
            not ContactLeadCreationService._is_positive_int(lead_id)
            or not ContactLeadCreationService._is_positive_int(returned_company_id)
            or not ContactLeadCreationService._is_positive_int(returned_contact_id)
            or returned_company_id != company_id
            or returned_contact_id != contact_id
            or type(status) is not str
            or status != "NEW"
            or source is not None
            or notes is not None
        ):
            raise ContactLeadCreationConsistencyError(_INCONSISTENT_STATE)

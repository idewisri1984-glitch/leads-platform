import re
from typing import Protocol, cast

from app.modules.contact_discovery.candidate_promotion_schemas import (
    ContactDiscoveryCandidatePromotionResult,
)
from app.modules.contact_discovery.models import ContactDiscoveryCandidateStatus
from app.modules.contact_discovery.normalization import normalize_discovered_email

_CONTACT_SOURCE = "CONTACT_DISCOVERY"
_EXTERNAL_ID_PREFIX = "contact-discovery-candidate:"
_INVALID_DATA = "Candidate promotion data is invalid."
_INCONSISTENT_STATE = "Candidate promotion state is inconsistent."
_NOT_ELIGIBLE = "Candidate is not eligible for promotion."
_NOT_FOUND = "Candidate was not found."
_WHITESPACE = re.compile(r"\s+")


class ContactDiscoveryCandidatePromotionError(ValueError):
    pass


class ContactDiscoveryCandidatePromotionNotFoundError(ContactDiscoveryCandidatePromotionError):
    pass


class ContactDiscoveryCandidateNotEligibleError(ContactDiscoveryCandidatePromotionError):
    pass


class ContactDiscoveryCandidatePromotionInvalidDataError(ContactDiscoveryCandidatePromotionError):
    pass


class ContactDiscoveryCandidatePromotionConsistencyError(ContactDiscoveryCandidatePromotionError):
    pass


class PromotionCandidateRecord(Protocol):
    id: int
    company_id: int
    name: str | None
    title: str | None
    email: str | None
    normalized_email: str | None
    phone: str | None
    discovery_status: ContactDiscoveryCandidateStatus
    promoted_contact_id: int | None


class PromotionContactRecord(Protocol):
    id: int
    company_id: int


class CandidatePromotionStagingRepository(Protocol):
    def get_candidate_for_promotion(
        self,
        company_id: int,
        candidate_id: int,
    ) -> PromotionCandidateRecord | None: ...

    def link_promoted_contact(
        self,
        company_id: int,
        candidate_id: int,
        contact_id: int,
    ) -> PromotionCandidateRecord: ...


class CandidatePromotionContactRepository(Protocol):
    def acquire_promotion_scope(self, company_id: int) -> None: ...

    def get_for_company(
        self,
        company_id: int,
        contact_id: int,
    ) -> PromotionContactRecord | None: ...

    def find_promotion_duplicate_by_email(
        self,
        company_id: int,
        normalized_email: str,
    ) -> PromotionContactRecord | None: ...

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
    ) -> PromotionContactRecord: ...


class ContactDiscoveryCandidatePromotionService:
    def __init__(
        self,
        staging_repository: CandidatePromotionStagingRepository,
        contact_repository: CandidatePromotionContactRepository,
    ) -> None:
        self.staging_repository = staging_repository
        self.contact_repository = contact_repository

    def promote(
        self,
        company_id: int,
        candidate_id: int,
    ) -> ContactDiscoveryCandidatePromotionResult:
        self._validate_id(company_id)
        self._validate_id(candidate_id)
        try:
            self.contact_repository.acquire_promotion_scope(company_id)
        except (TypeError, ValueError):
            raise ContactDiscoveryCandidatePromotionNotFoundError(_NOT_FOUND) from None

        candidate = self.staging_repository.get_candidate_for_promotion(
            company_id,
            candidate_id,
        )
        if candidate is None:
            raise ContactDiscoveryCandidatePromotionNotFoundError(_NOT_FOUND)
        self._validate_candidate_identity(candidate, company_id, candidate_id)
        status = self._normalize_status(candidate.discovery_status)

        if status is ContactDiscoveryCandidateStatus.PROMOTED:
            return self._resolve_existing_promotion(candidate, company_id, candidate_id)
        if status is not ContactDiscoveryCandidateStatus.REVIEWED:
            raise ContactDiscoveryCandidateNotEligibleError(_NOT_ELIGIBLE)
        if candidate.promoted_contact_id is not None:
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)

        first_name, last_name = self._map_name(candidate.name)
        job_title = self._normalize_optional_text(candidate.title, 150)
        phone = self._normalize_optional_text(candidate.phone, 100)
        canonical_email = self._resolve_email(
            candidate.email,
            candidate.normalized_email,
        )
        contact = None
        if canonical_email is not None:
            try:
                contact = self.contact_repository.find_promotion_duplicate_by_email(
                    company_id,
                    canonical_email,
                )
            except (TypeError, ValueError):
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA) from None

        created_contact = contact is None
        if contact is None:
            try:
                contact = self.contact_repository.create_for_promotion(
                    company_id=company_id,
                    first_name=first_name,
                    last_name=last_name,
                    job_title=job_title,
                    email=canonical_email,
                    phone=phone,
                    source=_CONTACT_SOURCE,
                    external_id=f"{_EXTERNAL_ID_PREFIX}{candidate_id}",
                    status="NEW",
                )
            except (TypeError, ValueError):
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA) from None
        contact_id = self._validate_contact(contact, company_id)

        try:
            linked = self.staging_repository.link_promoted_contact(
                company_id,
                candidate_id,
                contact_id,
            )
        except (TypeError, ValueError):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE) from None
        self._validate_candidate_identity(linked, company_id, candidate_id)
        linked_status = self._normalize_status(linked.discovery_status)
        if (
            linked_status is not ContactDiscoveryCandidateStatus.PROMOTED
            or not self._is_positive_int(linked.promoted_contact_id)
            or linked.promoted_contact_id != contact_id
        ):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)
        return ContactDiscoveryCandidatePromotionResult(
            candidate_id=candidate_id,
            company_id=company_id,
            contact_id=contact_id,
            previous_status=ContactDiscoveryCandidateStatus.REVIEWED,
            current_status=ContactDiscoveryCandidateStatus.PROMOTED,
            created_contact=created_contact,
            changed=True,
        )

    def _resolve_existing_promotion(
        self,
        candidate: PromotionCandidateRecord,
        company_id: int,
        candidate_id: int,
    ) -> ContactDiscoveryCandidatePromotionResult:
        contact_id = candidate.promoted_contact_id
        if not self._is_positive_int(contact_id):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)
        valid_contact_id = cast(int, contact_id)
        try:
            contact = self.contact_repository.get_for_company(company_id, valid_contact_id)
        except (TypeError, ValueError):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE) from None
        if contact is None or self._validate_contact(contact, company_id) != valid_contact_id:
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)
        return ContactDiscoveryCandidatePromotionResult(
            candidate_id=candidate_id,
            company_id=company_id,
            contact_id=valid_contact_id,
            previous_status=ContactDiscoveryCandidateStatus.PROMOTED,
            current_status=ContactDiscoveryCandidateStatus.PROMOTED,
            created_contact=False,
            changed=False,
        )

    @staticmethod
    def _validate_id(value: object) -> None:
        if not ContactDiscoveryCandidatePromotionService._is_positive_int(value):
            raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return type(value) is int and value > 0

    @staticmethod
    def _validate_candidate_identity(
        candidate: PromotionCandidateRecord,
        company_id: int,
        candidate_id: int,
    ) -> None:
        if (
            not ContactDiscoveryCandidatePromotionService._is_positive_int(candidate.id)
            or not ContactDiscoveryCandidatePromotionService._is_positive_int(candidate.company_id)
            or candidate.id != candidate_id
            or candidate.company_id != company_id
        ):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)

    @staticmethod
    def _normalize_status(value: object) -> ContactDiscoveryCandidateStatus:
        if isinstance(value, ContactDiscoveryCandidateStatus):
            return value
        if type(value) is not str:
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)
        try:
            return ContactDiscoveryCandidateStatus(value)
        except ValueError:
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE) from None

    @staticmethod
    def _normalize_required_text(value: object, maximum: int) -> str:
        if type(value) is not str or "\x00" in value or "<" in value or ">" in value:
            raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
        normalized = _WHITESPACE.sub(" ", value.strip())
        if not normalized or len(normalized) > maximum:
            raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
        return normalized

    @staticmethod
    def _normalize_optional_text(value: object, maximum: int) -> str | None:
        if value is None:
            return None
        if type(value) is not str or "\x00" in value or "<" in value or ">" in value:
            raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
        normalized = _WHITESPACE.sub(" ", value.strip())
        if not normalized:
            return None
        if len(normalized) > maximum:
            raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
        return normalized

    @classmethod
    def _map_name(cls, value: object) -> tuple[str, str | None]:
        normalized = cls._normalize_required_text(value, 201)
        first_name, separator, remainder = normalized.partition(" ")
        last_name = remainder if separator else None
        if len(first_name) > 100 or (last_name is not None and len(last_name) > 100):
            raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
        return first_name, last_name

    @staticmethod
    def _resolve_email(raw_email: object, stored_email: object) -> str | None:
        normalized_raw = None
        if raw_email is not None:
            if type(raw_email) is not str:
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
            try:
                normalized_raw = normalize_discovered_email(raw_email)
            except ValueError:
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA) from None
            if normalized_raw is None:
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)

        normalized_stored = None
        if stored_email is not None:
            if type(stored_email) is not str:
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)
            try:
                normalized_stored = normalize_discovered_email(stored_email)
            except ValueError:
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA) from None
            if normalized_stored is None or normalized_stored != stored_email:
                raise ContactDiscoveryCandidatePromotionInvalidDataError(_INVALID_DATA)

        if (
            normalized_raw is not None
            and normalized_stored is not None
            and normalized_raw != normalized_stored
        ):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)
        return normalized_stored or normalized_raw

    @staticmethod
    def _validate_contact(
        contact: PromotionContactRecord,
        company_id: int,
    ) -> int:
        if (
            not ContactDiscoveryCandidatePromotionService._is_positive_int(contact.id)
            or not ContactDiscoveryCandidatePromotionService._is_positive_int(contact.company_id)
            or contact.company_id != company_id
        ):
            raise ContactDiscoveryCandidatePromotionConsistencyError(_INCONSISTENT_STATE)
        return contact.id

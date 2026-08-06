from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.modules.company.models import Company
from app.modules.contact_discovery.models import (
    CompanyContactDiscoveryState,
    ContactDiscoveryCandidate,
    ContactDiscoveryCandidateStatus,
    ContactDiscoveryStatus,
)
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
    clean_discovered_text,
    normalize_discovered_email,
    normalize_source_for_deduplication,
)
from app.modules.contact_discovery.schemas import (
    ContactDiscoveryCandidateCreate,
    ContactDiscoveryCandidateRead,
    ContactDiscoveryCandidateUpsertResult,
    ContactDiscoveryPersistedCandidateRaw,
)

_UNSET = object()


class ContactDiscoveryCandidateRepositoryNotFoundError(ValueError):
    pass


class ContactDiscoveryCandidateRepositoryTransitionError(ValueError):
    pass


def _normalize_persisted_candidate_status(
    value: object,
) -> ContactDiscoveryCandidateStatus:
    if isinstance(value, ContactDiscoveryCandidateStatus):
        return value
    if type(value) is not str:
        raise ContactDiscoveryCandidateRepositoryTransitionError(
            "Candidate status transition is not allowed."
        )
    try:
        return ContactDiscoveryCandidateStatus(value)
    except ValueError:
        raise ContactDiscoveryCandidateRepositoryTransitionError(
            "Candidate status transition is not allowed."
        ) from None


@dataclass(frozen=True)
class ContactDiscoveryCandidateStatusTransitionResult:
    candidate: ContactDiscoveryCandidate
    previous_status: ContactDiscoveryCandidateStatus
    current_status: ContactDiscoveryCandidateStatus
    changed: bool


class ContactDiscoveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_state_by_company_id(self, company_id: int) -> CompanyContactDiscoveryState | None:
        return self.session.scalar(
            select(CompanyContactDiscoveryState).where(
                CompanyContactDiscoveryState.company_id == company_id
            )
        )

    def get_or_create_state(self, company_id: int) -> tuple[CompanyContactDiscoveryState, bool]:
        existing = self.get_state_by_company_id(company_id)
        if existing is not None:
            return existing, False
        state = CompanyContactDiscoveryState(company_id=company_id)
        self.session.add(state)
        self.session.flush()
        return state, True

    def update_state(
        self,
        company_id: int,
        *,
        provider: str | None | object = _UNSET,
        discovery_status: ContactDiscoveryStatus | object = _UNSET,
        checked_at: datetime | None | object = _UNSET,
        last_error: str | None | object = _UNSET,
    ) -> CompanyContactDiscoveryState:
        state, _ = self.get_or_create_state(company_id)
        values = {
            "provider": provider,
            "discovery_status": discovery_status,
            "checked_at": checked_at,
            "last_error": last_error,
        }
        for field, value in values.items():
            if value is not _UNSET:
                setattr(state, field, value)
        self.session.add(state)
        self.session.flush()
        return state

    def list_states_for_project(
        self, project_id: int, limit: int, offset: int = 0
    ) -> list[CompanyContactDiscoveryState]:
        self._validate_pagination(limit, offset)
        statement = (
            select(CompanyContactDiscoveryState)
            .join(Company)
            .where(Company.project_id == project_id)
            .order_by(Company.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def get_candidate(self, candidate_id: int) -> ContactDiscoveryCandidate | None:
        return self.session.get(ContactDiscoveryCandidate, candidate_id)

    def get_candidate_for_company(
        self, company_id: int, candidate_id: int
    ) -> ContactDiscoveryCandidate | None:
        self._validate_positive_id(company_id, "Company")
        self._validate_positive_id(candidate_id, "Candidate")
        return self.session.scalar(
            select(ContactDiscoveryCandidate).where(
                ContactDiscoveryCandidate.id == candidate_id,
                ContactDiscoveryCandidate.company_id == company_id,
            )
        )

    def get_candidate_for_promotion(
        self, company_id: int, candidate_id: int
    ) -> ContactDiscoveryCandidate | None:
        return self._get_candidate_for_status_mutation(company_id, candidate_id)

    def _get_candidate_for_status_mutation(
        self, company_id: int, candidate_id: int
    ) -> ContactDiscoveryCandidate | None:
        self._validate_positive_id(company_id, "Company")
        self._validate_positive_id(candidate_id, "Candidate")
        statement = (
            select(ContactDiscoveryCandidate)
            .where(
                ContactDiscoveryCandidate.company_id == company_id,
                ContactDiscoveryCandidate.id == candidate_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def list_candidates_for_company(
        self,
        company_id: int,
        limit: int = 100,
        offset: int = 0,
        candidate_status: ContactDiscoveryCandidateStatus | None = None,
    ) -> list[ContactDiscoveryCandidate]:
        self._validate_positive_id(company_id, "Company")
        self._validate_pagination(limit, offset, maximum=100)
        statement = (
            select(ContactDiscoveryCandidate)
            .where(ContactDiscoveryCandidate.company_id == company_id)
            .order_by(ContactDiscoveryCandidate.id)
            .limit(limit)
            .offset(offset)
        )
        if candidate_status is not None:
            if not isinstance(candidate_status, ContactDiscoveryCandidateStatus):
                raise ValueError("Invalid candidate status filter.")
            statement = statement.where(
                ContactDiscoveryCandidate.discovery_status == candidate_status
            )
        return list(self.session.scalars(statement))

    def set_candidate_status(
        self,
        company_id: int,
        candidate_id: int,
        candidate_status: ContactDiscoveryCandidateStatus,
    ) -> ContactDiscoveryCandidateStatusTransitionResult:
        self._validate_positive_id(company_id, "Company")
        self._validate_positive_id(candidate_id, "Candidate")
        if not isinstance(
            candidate_status, ContactDiscoveryCandidateStatus
        ) or candidate_status not in (
            ContactDiscoveryCandidateStatus.REVIEWED,
            ContactDiscoveryCandidateStatus.REJECTED,
        ):
            raise ValueError("Candidate target status is not allowed.")
        candidate = self._get_candidate_for_status_mutation(company_id, candidate_id)
        if candidate is None:
            raise ContactDiscoveryCandidateRepositoryNotFoundError("Candidate was not found.")
        previous_status = _normalize_persisted_candidate_status(candidate.discovery_status)
        allowed = {
            ContactDiscoveryCandidateStatus.DISCOVERED: (
                ContactDiscoveryCandidateStatus.REVIEWED,
                ContactDiscoveryCandidateStatus.REJECTED,
            ),
            ContactDiscoveryCandidateStatus.REVIEWED: (
                ContactDiscoveryCandidateStatus.REVIEWED,
                ContactDiscoveryCandidateStatus.REJECTED,
            ),
            ContactDiscoveryCandidateStatus.REJECTED: (ContactDiscoveryCandidateStatus.REJECTED,),
            ContactDiscoveryCandidateStatus.PROMOTED: (),
        }
        if candidate.promoted_contact_id is not None or candidate_status not in allowed.get(
            previous_status, ()
        ):
            raise ContactDiscoveryCandidateRepositoryTransitionError(
                "Candidate status transition is not allowed."
            )
        changed = previous_status != candidate_status
        if changed:
            candidate.discovery_status = candidate_status
            self.session.add(candidate)
            self.session.flush()
        return ContactDiscoveryCandidateStatusTransitionResult(
            candidate=candidate,
            previous_status=previous_status,
            current_status=candidate_status,
            changed=changed,
        )

    def link_promoted_contact(
        self,
        company_id: int,
        candidate_id: int,
        contact_id: int,
    ) -> ContactDiscoveryCandidate:
        self._validate_positive_id(company_id, "Company")
        self._validate_positive_id(candidate_id, "Candidate")
        self._validate_positive_id(contact_id, "Contact")
        candidate = self.get_candidate_for_promotion(company_id, candidate_id)
        if candidate is None:
            raise ValueError("Candidate was not found.")
        if (
            candidate.discovery_status != ContactDiscoveryCandidateStatus.REVIEWED
            or candidate.promoted_contact_id is not None
        ):
            raise ValueError("Candidate is not eligible for promotion.")
        candidate_table = cast(Table, ContactDiscoveryCandidate.__table__)
        contacts = candidate_table.metadata.tables.get("contacts")
        if contacts is None:
            raise ValueError("Contact is not available for candidate promotion.")
        available_contact_id = self.session.scalar(
            select(contacts.c.id)
            .where(
                contacts.c.id == contact_id,
                contacts.c.company_id == company_id,
            )
            .with_for_update()
        )
        if available_contact_id is None:
            raise ValueError("Contact is not available for candidate promotion.")
        candidate.discovery_status = ContactDiscoveryCandidateStatus.PROMOTED
        candidate.promoted_contact_id = available_contact_id
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def list_candidates_for_project(
        self, project_id: int, limit: int, offset: int = 0
    ) -> list[ContactDiscoveryCandidate]:
        self._validate_pagination(limit, offset)
        statement = (
            select(ContactDiscoveryCandidate)
            .join(Company)
            .where(Company.project_id == project_id)
            .order_by(Company.id, ContactDiscoveryCandidate.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def upsert_candidate(
        self,
        company_id: int,
        candidate: ContactDiscoveryCandidateCreate,
    ) -> ContactDiscoveryCandidateUpsertResult:
        if candidate.company_id != company_id:
            raise ValueError("Candidate company ID does not match repository scope.")
        normalized_email = normalize_discovered_email(candidate.email)
        if candidate.source_url is not None:
            normalize_source_for_deduplication(candidate.source_url)
        deduplication_key = build_contact_candidate_deduplication_key(
            email=candidate.email,
            name=candidate.name,
            title=candidate.title,
            source_url=candidate.source_url,
        )
        existing = self.session.scalar(
            select(ContactDiscoveryCandidate).where(
                ContactDiscoveryCandidate.company_id == company_id,
                ContactDiscoveryCandidate.deduplication_key == deduplication_key,
            )
        )
        if existing is None:
            created = ContactDiscoveryCandidate(
                company_id=company_id,
                promoted_contact_id=None,
                name=clean_discovered_text(candidate.name),
                title=clean_discovered_text(candidate.title),
                email=clean_discovered_text(candidate.email),
                normalized_email=normalized_email,
                phone=clean_discovered_text(candidate.phone),
                source_url=clean_discovered_text(candidate.source_url),
                source_type=candidate.source_type,
                confidence=candidate.confidence,
                discovery_status=ContactDiscoveryCandidateStatus.DISCOVERED,
                deduplication_key=deduplication_key,
                notes=clean_discovered_text(candidate.notes),
                last_error=clean_discovered_text(candidate.last_error),
            )
            self.session.add(created)
            self.session.flush()
            return self._result(created, created=True)

        if existing.discovery_status != ContactDiscoveryCandidateStatus.DISCOVERED:
            return self._result(existing, protected=True)

        changed = False
        incoming: dict[str, Any] = {
            "name": clean_discovered_text(candidate.name),
            "title": clean_discovered_text(candidate.title),
            "email": clean_discovered_text(candidate.email),
            "normalized_email": normalized_email,
            "phone": clean_discovered_text(candidate.phone),
            "source_url": clean_discovered_text(candidate.source_url),
            "notes": clean_discovered_text(candidate.notes),
            "last_error": clean_discovered_text(candidate.last_error),
        }
        for field, value in incoming.items():
            if getattr(existing, field) is None and value is not None:
                setattr(existing, field, value)
                changed = True
        if candidate.confidence > existing.confidence:
            existing.confidence = candidate.confidence
            changed = True
        if changed:
            self.session.add(existing)
            self.session.flush()
        return self._result(existing, updated=changed)

    @staticmethod
    def _result(
        candidate: ContactDiscoveryCandidate,
        *,
        created: bool = False,
        updated: bool = False,
        protected: bool = False,
    ) -> ContactDiscoveryCandidateUpsertResult:
        read = ContactDiscoveryCandidateRead.model_validate(candidate)
        return ContactDiscoveryCandidateUpsertResult(
            candidate=read,
            persisted_candidate=ContactDiscoveryPersistedCandidateRaw(
                id=read.id,
                company_id=read.company_id,
                promoted_contact_id=read.promoted_contact_id,
                name=read.name,
                title=read.title,
                email=read.email,
                normalized_email=read.normalized_email,
                phone=read.phone,
                source_url=read.source_url,
                source_type=read.source_type,
                confidence=float(read.confidence) / 100.0,
                discovery_status=read.discovery_status,
                deduplication_key=read.deduplication_key,
                notes=read.notes,
                last_error=read.last_error,
            ),
            created=created,
            updated=updated,
            protected=protected,
        )

    @staticmethod
    def _validate_pagination(limit: int, offset: int, maximum: int | None = None) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Limit must be greater than zero.")
        if maximum is not None and limit > maximum:
            raise ValueError("Limit exceeds the allowed maximum.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Offset must not be negative.")

    @staticmethod
    def _validate_positive_id(value: int, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} ID must be a positive integer.")

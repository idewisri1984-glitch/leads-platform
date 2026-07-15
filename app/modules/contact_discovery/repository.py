from datetime import datetime
from typing import Any

from sqlalchemy import select
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
)

_UNSET = object()


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

    def list_candidates_for_company(self, company_id: int) -> list[ContactDiscoveryCandidate]:
        statement = (
            select(ContactDiscoveryCandidate)
            .where(ContactDiscoveryCandidate.company_id == company_id)
            .order_by(ContactDiscoveryCandidate.id)
        )
        return list(self.session.scalars(statement))

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
        return ContactDiscoveryCandidateUpsertResult(
            candidate=ContactDiscoveryCandidateRead.model_validate(candidate),
            created=created,
            updated=updated,
            protected=protected,
        )

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if limit <= 0:
            raise ValueError("Limit must be greater than zero.")
        if offset < 0:
            raise ValueError("Offset must not be negative.")

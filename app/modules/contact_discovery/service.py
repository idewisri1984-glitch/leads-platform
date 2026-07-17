from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.modules.contact_discovery.models import ContactDiscoveryStatus
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
    normalize_source_for_deduplication,
)
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate
from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProviderResult

_PROVIDER_INVALID_RESULT = "provider_invalid_result"
_PROVIDER_FAILED = "provider_failed"
_ALLOWED_PROVIDER_ERRORS = frozenset(
    {
        "invalid_website_url",
        "homepage_fetch_failed",
        "secondary_page_fetch_failed",
        "page_parse_failed",
        _PROVIDER_FAILED,
        _PROVIDER_INVALID_RESULT,
    }
)


class ContactDiscoveryProvider(Protocol):
    provider_name: str

    def discover(
        self,
        *,
        company_id: int,
        website_url: str,
    ) -> WebsiteContactDiscoveryProviderResult: ...


@dataclass(frozen=True)
class ContactDiscoveryRunResult:
    company_id: int
    dry_run: bool
    status: ContactDiscoveryStatus
    candidates: tuple[ContactDiscoveryCandidateCreate, ...] = ()
    attempted_pages: int = 0
    successful_pages: int = 0
    errors: tuple[str, ...] = ()
    candidate_upserts: int = 0
    state_persisted: bool = False
    selected_urls: int = 0
    limited_link_scan: bool = False


@dataclass(frozen=True)
class _ValidatedProviderResult:
    candidates: tuple[ContactDiscoveryCandidateCreate, ...]
    attempted_pages: int
    successful_pages: int
    errors: tuple[str, ...]
    selected_urls: int
    limited_link_scan: bool


class ContactDiscoveryService:
    def __init__(
        self,
        repository: ContactDiscoveryRepository,
        provider: ContactDiscoveryProvider,
    ) -> None:
        self.repository = repository
        self.provider = provider

    def run(
        self,
        *,
        company_id: int,
        website_url: str,
        dry_run: bool,
    ) -> ContactDiscoveryRunResult:
        if company_id <= 0:
            raise ValueError("Company ID must be greater than zero.")

        if not website_url.strip():
            validated = self._failed_result(_PROVIDER_INVALID_RESULT)
        else:
            try:
                provider_result = self.provider.discover(
                    company_id=company_id,
                    website_url=website_url,
                )
            except Exception:
                validated = self._failed_result(_PROVIDER_FAILED)
            else:
                validated = self._validate_provider_result(provider_result, company_id)

        status = self._status_for(validated)
        if dry_run:
            return self._run_result(
                company_id=company_id,
                dry_run=True,
                status=status,
                result=validated,
            )

        candidate_upserts = 0
        if status in {ContactDiscoveryStatus.SUCCEEDED, ContactDiscoveryStatus.PARTIAL}:
            for candidate in validated.candidates:
                self.repository.upsert_candidate(company_id, candidate)
                candidate_upserts += 1

        self.repository.update_state(
            company_id,
            provider=self._safe_provider_name(),
            discovery_status=status,
            checked_at=datetime.now(UTC),
            last_error=validated.errors[0] if validated.errors else None,
        )
        return self._run_result(
            company_id=company_id,
            dry_run=False,
            status=status,
            result=validated,
            candidate_upserts=candidate_upserts,
            state_persisted=True,
        )

    @staticmethod
    def _validate_provider_result(
        result: object,
        company_id: int,
    ) -> _ValidatedProviderResult:
        if not isinstance(result, WebsiteContactDiscoveryProviderResult):
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)

        counters = (result.attempted_pages, result.successful_pages, result.selected_urls)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters
        ):
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
        if result.successful_pages > result.attempted_pages:
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
        if not isinstance(result.limited_link_scan, bool):
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
        if not ContactDiscoveryService._is_sequence(result.candidates) or not (
            ContactDiscoveryService._is_sequence(result.errors)
        ):
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)

        candidates: list[ContactDiscoveryCandidateCreate] = []
        for candidate in result.candidates:
            if not isinstance(candidate, ContactDiscoveryCandidateCreate):
                return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
            try:
                validated_candidate = ContactDiscoveryCandidateCreate.model_validate(
                    candidate.model_dump()
                )
            except (TypeError, ValueError):
                return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
            if validated_candidate.company_id != company_id:
                return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
            try:
                if validated_candidate.source_url is not None:
                    normalize_source_for_deduplication(validated_candidate.source_url)
                build_contact_candidate_deduplication_key(
                    email=validated_candidate.email,
                    name=validated_candidate.name,
                    title=validated_candidate.title,
                    source_url=validated_candidate.source_url,
                )
            except ValueError:
                return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
            candidates.append(validated_candidate)

        errors: list[str] = []
        for error in result.errors:
            if not isinstance(error, str):
                return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
            sanitized = error if error in _ALLOWED_PROVIDER_ERRORS else _PROVIDER_FAILED
            if sanitized not in errors:
                errors.append(sanitized)

        if result.successful_pages == 0 and not errors:
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)
        if candidates and result.successful_pages == 0:
            return ContactDiscoveryService._failed_result(_PROVIDER_INVALID_RESULT)

        return _ValidatedProviderResult(
            candidates=tuple(candidates),
            attempted_pages=result.attempted_pages,
            successful_pages=result.successful_pages,
            errors=tuple(errors),
            selected_urls=result.selected_urls,
            limited_link_scan=result.limited_link_scan,
        )

    @staticmethod
    def _is_sequence(value: object) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)

    @staticmethod
    def _failed_result(error: str) -> _ValidatedProviderResult:
        return _ValidatedProviderResult(
            candidates=(),
            attempted_pages=0,
            successful_pages=0,
            errors=(error,),
            selected_urls=0,
            limited_link_scan=False,
        )

    @staticmethod
    def _status_for(result: _ValidatedProviderResult) -> ContactDiscoveryStatus:
        if result.successful_pages == 0 and result.errors:
            return ContactDiscoveryStatus.FAILED
        if result.successful_pages > 0 and result.errors:
            return ContactDiscoveryStatus.PARTIAL
        if result.candidates:
            return ContactDiscoveryStatus.SUCCEEDED
        return ContactDiscoveryStatus.NOT_FOUND

    def _safe_provider_name(self) -> str | None:
        provider_name = getattr(self.provider, "provider_name", None)
        if not isinstance(provider_name, str):
            return None
        provider_name = provider_name.strip()
        return provider_name[:100] or None

    @staticmethod
    def _run_result(
        *,
        company_id: int,
        dry_run: bool,
        status: ContactDiscoveryStatus,
        result: _ValidatedProviderResult,
        candidate_upserts: int = 0,
        state_persisted: bool = False,
    ) -> ContactDiscoveryRunResult:
        return ContactDiscoveryRunResult(
            company_id=company_id,
            dry_run=dry_run,
            status=status,
            candidates=result.candidates,
            attempted_pages=result.attempted_pages,
            successful_pages=result.successful_pages,
            errors=result.errors,
            candidate_upserts=candidate_upserts,
            state_persisted=state_persisted,
            selected_urls=result.selected_urls,
            limited_link_scan=result.limited_link_scan,
        )

from dataclasses import dataclass
from math import isfinite
from typing import Protocol, cast

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ..contact_discovery.models import (
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
    ContactDiscoveryStatus,
)
from ..contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
)
from ..contact_discovery.repository import ContactDiscoveryRepository
from ..contact_discovery.service import (
    ContactDiscoveryPersistedCandidate,
    ContactDiscoveryProvider,
    ContactDiscoveryRunResult,
    ContactDiscoveryService,
)
from .contact_plan_schemas import (
    AgentContactDecision,
    AgentContactDiscoveryStatus,
    AgentContactPlanInput,
    AgentContactPlanResult,
)

_INVALID = "Agent contact plan data is invalid."
_PROJECT_NOT_FOUND = "Project was not found."
_COMPANY_NOT_FOUND = "Company was not found."
_BINDING_MISMATCH = "Company does not belong to the supplied project."
_WEBSITE_MISSING = "Company website is required for contact planning."
_PROVIDER_FAILED = "Contact discovery provider failed."
_DISCOVERY_INVALID = "Contact discovery result is invalid."
_PERSISTENCE_FAILED = "Contact discovery state could not be persisted."
_SELECTION_FAILED = "Agent contact selection is inconsistent."
_INTERNAL_FAILED = "Agent contact plan failed."

_ROLE_GROUPS = (
    (
        "procurement",
        "purchasing",
        "sourcing",
        "buyer",
        "vendor",
        "partnership",
        "partnerships",
        "business development",
    ),
    (
        "founder",
        "co-founder",
        "cofounder",
        "owner",
        "principal",
        "managing partner",
        "president",
        "chief executive",
        "ceo",
    ),
    ("creative director", "design director", "interior design director", "studio director"),
    ("project director", "senior interior designer", "interior designer"),
)


class AgentContactPlanError(ValueError):
    pass


class AgentContactPlanInvalidDataError(AgentContactPlanError):
    pass


class AgentContactPlanProjectNotFoundError(AgentContactPlanError):
    pass


class AgentContactPlanCompanyNotFoundError(AgentContactPlanError):
    pass


class AgentContactPlanBindingMismatchError(AgentContactPlanError):
    pass


class AgentContactPlanWebsiteMissingError(AgentContactPlanError):
    pass


class AgentContactPlanProviderError(AgentContactPlanError):
    pass


class AgentContactPlanDiscoveryResultError(AgentContactPlanError):
    pass


class AgentContactPlanPersistenceError(AgentContactPlanError):
    pass


class AgentContactPlanSelectionConsistencyError(AgentContactPlanError):
    pass


class AgentContactPlanInternalError(AgentContactPlanError):
    pass


class ProjectLookup(Protocol):
    def get(self, project_id: int) -> object | None: ...


class CompanyLookup(Protocol):
    def get(self, company_id: int) -> object | None: ...


class _CompanyRecord(Protocol):
    id: object
    project_id: object
    name: object
    website: object


class ProviderFactory(Protocol):
    def __call__(self) -> ContactDiscoveryProvider: ...


class DiscoveryServiceFactory(Protocol):
    def __call__(
        self,
        repository: ContactDiscoveryRepository,
        provider: ContactDiscoveryProvider,
    ) -> ContactDiscoveryService: ...


@dataclass(frozen=True, slots=True)
class _CompanySnapshot:
    id: int
    project_id: int
    name: str
    website: str


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    candidate: ContactDiscoveryPersistedCandidate
    name: str
    title: str | None
    email: str | None
    phone: str | None
    source_url: str | None
    source_type: ContactDiscoverySourceType
    confidence: float
    role_priority: int


def _text(value: object, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError
        return None
    if type(value) is not str:
        raise ValueError
    value.encode("utf-8")
    normalized = " ".join(value.split())
    if required and not normalized:
        raise ValueError
    return normalized or None


def _enum[T](enum_type: type[T], value: object) -> T:
    if isinstance(value, enum_type):
        return value
    return enum_type(value)  # type: ignore[call-arg]


def _bounded(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum].rstrip()


def _phrase_tokens(value: str) -> tuple[str, ...]:
    separated = "".join(character if character.isalnum() else " " for character in value.casefold())
    return tuple(separated.split())


def _contains_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    width = len(phrase)
    return bool(width) and any(
        tokens[index : index + width] == phrase for index in range(len(tokens) - width + 1)
    )


def _strict_persisted_enum[T](enum_type: type[T], value: object) -> T:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise ValueError
    return enum_type(value)  # type: ignore[call-arg]


class AgentContactPlanService:
    def __init__(
        self,
        *,
        projects: ProjectLookup,
        companies: CompanyLookup,
        discovery_repository: ContactDiscoveryRepository,
        provider_factory: ProviderFactory,
        discovery_service_factory: DiscoveryServiceFactory = ContactDiscoveryService,
    ) -> None:
        self.projects = projects
        self.companies = companies
        self.discovery_repository = discovery_repository
        self.provider_factory = provider_factory
        self.discovery_service_factory = discovery_service_factory

    def plan(self, plan_input: AgentContactPlanInput) -> AgentContactPlanResult:
        data = self._validate_input(plan_input)
        try:
            project = self.projects.get(data.project_id)
        except Exception:
            raise AgentContactPlanInternalError(_INTERNAL_FAILED) from None
        project_id = getattr(project, "id", None)
        if project is None or type(project_id) is not int:
            raise AgentContactPlanProjectNotFoundError(_PROJECT_NOT_FOUND)
        if project_id != data.project_id:
            raise AgentContactPlanProjectNotFoundError(_PROJECT_NOT_FOUND)

        try:
            company_record = self.companies.get(data.company_id)
        except Exception:
            raise AgentContactPlanInternalError(_INTERNAL_FAILED) from None
        if company_record is None:
            raise AgentContactPlanCompanyNotFoundError(_COMPANY_NOT_FOUND)
        company = self._snapshot_company(company_record, data.company_id)
        if company.project_id != data.project_id:
            raise AgentContactPlanBindingMismatchError(_BINDING_MISMATCH)

        provider: ContactDiscoveryProvider | None = None
        primary = False
        try:
            try:
                provider = self.provider_factory()
            except Exception:
                raise AgentContactPlanProviderError(_PROVIDER_FAILED) from None
            provider_name = self._provider_name(provider)
            try:
                discovery = self.discovery_service_factory(self.discovery_repository, provider)
                raw = discovery.run(
                    company_id=company.id,
                    website_url=company.website,
                    dry_run=False,
                )
            except SQLAlchemyError:
                raise AgentContactPlanPersistenceError(_PERSISTENCE_FAILED) from None
            except AgentContactPlanError:
                raise
            except Exception:
                raise AgentContactPlanPersistenceError(_PERSISTENCE_FAILED) from None
            result = self._validate_discovery(raw, company.id)
            if result.status is ContactDiscoveryStatus.FAILED:
                if "provider_invalid_result" in result.errors:
                    raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
                raise AgentContactPlanProviderError(_PROVIDER_FAILED)
            eligible = self._eligible(result.persisted_candidates, company.id)
            return self._result(data, company, provider_name, result, eligible)
        except BaseException:
            primary = True
            raise
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        if not primary:
                            raise AgentContactPlanInternalError(_INTERNAL_FAILED) from None

    @staticmethod
    def _validate_input(value: AgentContactPlanInput) -> AgentContactPlanInput:
        if type(value) is not AgentContactPlanInput:
            raise AgentContactPlanInvalidDataError(_INVALID)
        try:
            return AgentContactPlanInput.model_validate(value.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise AgentContactPlanInvalidDataError(_INVALID) from None

    @staticmethod
    def _snapshot_company(record: object, expected_id: int) -> _CompanySnapshot:
        company_record = cast(_CompanyRecord, record)
        try:
            company_id = company_record.id
            project_id = company_record.project_id
            name = _text(company_record.name, required=True)
            website = _text(company_record.website, required=True)
        except (AttributeError, TypeError, ValueError, UnicodeEncodeError):
            if (
                getattr(record, "website", None) is None
                or not str(getattr(record, "website", "")).strip()
            ):
                raise AgentContactPlanWebsiteMissingError(_WEBSITE_MISSING) from None
            raise AgentContactPlanInternalError(_INTERNAL_FAILED) from None
        if type(company_id) is not int or company_id != expected_id:
            raise AgentContactPlanCompanyNotFoundError(_COMPANY_NOT_FOUND)
        if type(project_id) is not int or project_id <= 0 or name is None or website is None:
            raise AgentContactPlanInternalError(_INTERNAL_FAILED)
        return _CompanySnapshot(company_id, project_id, name, website)

    @staticmethod
    def _provider_name(provider: ContactDiscoveryProvider) -> str:
        try:
            name = _text(provider.provider_name, required=True)
        except (AttributeError, TypeError, ValueError, UnicodeEncodeError):
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID) from None
        if name is None or len(name) > 100:
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
        return name

    @staticmethod
    def _validate_discovery(raw: object, company_id: int) -> ContactDiscoveryRunResult:
        if type(raw) is not ContactDiscoveryRunResult:
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
        try:
            status = _enum(ContactDiscoveryStatus, raw.status)
            persisted = tuple(raw.persisted_candidates)
            candidates = tuple(raw.candidates)
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID) from None
        counters = (
            raw.attempted_pages,
            raw.successful_pages,
            raw.selected_urls,
            raw.candidate_upserts,
        )
        if (
            type(raw.company_id) is not int
            or raw.company_id != company_id
            or raw.dry_run is not False
            or raw.state_persisted is not True
            or status is ContactDiscoveryStatus.PENDING
            or any(type(value) is not int or value < 0 for value in counters)
            or raw.successful_pages > raw.attempted_pages
            or type(raw.limited_link_scan) is not bool
            or type(raw.errors) is not tuple
        ):
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
        if status in {ContactDiscoveryStatus.SUCCEEDED, ContactDiscoveryStatus.PARTIAL}:
            if not (len(candidates) == len(persisted) == raw.candidate_upserts):
                raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
            unique_persisted: dict[int, ContactDiscoveryPersistedCandidate] = {}
            for source, stored in zip(candidates, persisted, strict=True):
                try:
                    AgentContactPlanService._validate_persisted_candidate(stored, company_id)
                    expected_key = build_contact_candidate_deduplication_key(
                        email=source.email,
                        name=source.name,
                        title=source.title,
                        source_url=source.source_url,
                    )
                except (AttributeError, TypeError, ValueError):
                    raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID) from None
                if stored.company_id != company_id or stored.deduplication_key != expected_key:
                    raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
                existing = unique_persisted.get(stored.id)
                if existing is not None and existing.deduplication_key != stored.deduplication_key:
                    raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
                unique_persisted[stored.id] = stored
            persisted = tuple(unique_persisted.values())
        elif raw.candidate_upserts or persisted or candidates:
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
        return ContactDiscoveryRunResult(
            company_id=company_id,
            dry_run=False,
            status=status,
            candidates=candidates,
            attempted_pages=raw.attempted_pages,
            successful_pages=raw.successful_pages,
            errors=tuple(raw.errors),
            candidate_upserts=raw.candidate_upserts,
            state_persisted=True,
            selected_urls=raw.selected_urls,
            limited_link_scan=raw.limited_link_scan,
            persisted_candidates=persisted,
        )

    @staticmethod
    def _validate_persisted_candidate(
        candidate: object, company_id: int
    ) -> ContactDiscoveryPersistedCandidate:
        if type(candidate) is not ContactDiscoveryPersistedCandidate:
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
        string_values = (
            candidate.name,
            candidate.title,
            candidate.email,
            candidate.phone,
            candidate.source_url,
        )
        try:
            _strict_persisted_enum(ContactDiscoveryCandidateStatus, candidate.discovery_status)
            _strict_persisted_enum(ContactDiscoverySourceType, candidate.source_type)
        except (TypeError, ValueError):
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID) from None
        if (
            type(candidate.id) is not int
            or candidate.id <= 0
            or type(candidate.company_id) is not int
            or candidate.company_id <= 0
            or candidate.company_id != company_id
            or (
                candidate.promoted_contact_id is not None
                and (
                    type(candidate.promoted_contact_id) is not int
                    or candidate.promoted_contact_id <= 0
                )
            )
            or any(value is not None and type(value) is not str for value in string_values)
            or type(candidate.confidence) is not float
            or not isfinite(candidate.confidence)
            or not 0.0 <= candidate.confidence <= 1.0
            or type(candidate.deduplication_key) is not str
            or not candidate.deduplication_key.strip()
        ):
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID)
        return candidate

    @staticmethod
    def _eligible(
        candidates: tuple[ContactDiscoveryPersistedCandidate, ...], company_id: int
    ) -> tuple[_RankedCandidate, ...]:
        eligible: list[_RankedCandidate] = []
        for candidate in candidates:
            try:
                status = _strict_persisted_enum(
                    ContactDiscoveryCandidateStatus, candidate.discovery_status
                )
                source_type = _strict_persisted_enum(
                    ContactDiscoverySourceType, candidate.source_type
                )
                name = _text(candidate.name, required=True)
                title = _text(candidate.title)
                email = _text(candidate.email)
                phone = _text(candidate.phone)
                source_url = _text(candidate.source_url)
            except (TypeError, ValueError, UnicodeEncodeError):
                continue
            if (
                status is not ContactDiscoveryCandidateStatus.DISCOVERED
                or type(candidate.id) is not int
                or candidate.id <= 0
                or candidate.company_id != company_id
                or not candidate.deduplication_key.strip()
                or type(candidate.confidence) is not float
                or not isfinite(candidate.confidence)
                or not 0.0 <= candidate.confidence <= 1.0
                or name is None
            ):
                continue
            eligible.append(
                _RankedCandidate(
                    candidate=candidate,
                    name=name,
                    title=title,
                    email=email,
                    phone=phone,
                    source_url=source_url,
                    source_type=source_type,
                    confidence=candidate.confidence,
                    role_priority=AgentContactPlanService._role_priority(title),
                )
            )
        eligible.sort(
            key=lambda item: (
                item.role_priority,
                0 if item.email is not None else 1,
                -item.confidence,
                item.candidate.id,
            )
        )
        return tuple(eligible)

    @staticmethod
    def _role_priority(title: str | None) -> int:
        tokens = _phrase_tokens(title or "")
        for priority, markers in enumerate(_ROLE_GROUPS):
            if any(_contains_phrase(tokens, _phrase_tokens(marker)) for marker in markers):
                return priority
        return 4

    @staticmethod
    def _result(
        data: AgentContactPlanInput,
        company: _CompanySnapshot,
        provider_name: str,
        discovery: ContactDiscoveryRunResult,
        eligible: tuple[_RankedCandidate, ...],
    ) -> AgentContactPlanResult:
        try:
            status = AgentContactDiscoveryStatus(discovery.status)
        except ValueError:
            raise AgentContactPlanDiscoveryResultError(_DISCOVERY_INVALID) from None
        common = dict(
            project_id=data.project_id,
            company_id=company.id,
            company_name=company.name,
            company_website=company.website,
            goal=data.goal,
            discovery_status=status,
            provider_name=provider_name,
            provider_call_count=1,
            attempted_pages=discovery.attempted_pages,
            successful_pages=discovery.successful_pages,
            selected_urls=discovery.selected_urls,
            limited_link_scan=discovery.limited_link_scan,
            candidate_upsert_count=discovery.candidate_upserts,
            staged_candidate_count=len(discovery.persisted_candidates),
            eligible_candidate_count=len(eligible),
            human_review_required=True,
            staging_mutated=True,
            contact_mutation_count=0,
            lead_mutation_count=0,
            task_mutation_count=0,
        )
        if not eligible:
            return AgentContactPlanResult(
                **common,
                decision=AgentContactDecision.NO_SELECTION,
                selected_candidate_id=None,
                selected_contact_name=None,
                selected_contact_title=None,
                selected_contact_email=None,
                selected_contact_phone=None,
                selected_contact_source_url=None,
                selected_contact_source_type=None,
                selected_contact_confidence=None,
                selection_rationale=(
                    "No eligible named DISCOVERED contact was returned by the current provider run."
                ),
                proposed_lead_title=None,
                proposed_task_title=None,
                proposed_task_description=None,
            )
        selected = eligible[0]
        lead_title = _bounded(f"Bohemia Bali partnership — {company.name}", 255)
        task_title = _bounded(f"Review and prepare outreach to {selected.name}", 255)
        title_detail = f" with title {selected.title}" if selected.title else ""
        fixed = (
            "A human must verify this contact before any action. No outreach has been sent, "
            "and no Lead or Task has been created. "
            f"Selected person: {selected.name}{title_detail}. Company: {company.name}. "
            "Prepare a personalized Bohemia Bali partnership message. Goal: "
        )
        description = _bounded(fixed + data.goal, 4000)
        try:
            return AgentContactPlanResult(
                **common,
                decision=AgentContactDecision.SELECT,
                selected_candidate_id=selected.candidate.id,
                selected_contact_name=selected.name,
                selected_contact_title=selected.title,
                selected_contact_email=selected.email,
                selected_contact_phone=selected.phone,
                selected_contact_source_url=selected.source_url,
                selected_contact_source_type=selected.source_type,
                selected_contact_confidence=selected.confidence,
                selection_rationale=(
                    f"Selected current-run candidate {selected.candidate.id} using role "
                    "priority, email availability, confidence, and persisted ID."
                ),
                proposed_lead_title=lead_title,
                proposed_task_title=task_title,
                proposed_task_description=description,
            )
        except (ValidationError, TypeError, ValueError):
            raise AgentContactPlanSelectionConsistencyError(_SELECTION_FAILED) from None


__all__ = [
    "AgentContactPlanBindingMismatchError",
    "AgentContactPlanCompanyNotFoundError",
    "AgentContactPlanDiscoveryResultError",
    "AgentContactPlanError",
    "AgentContactPlanInternalError",
    "AgentContactPlanInvalidDataError",
    "AgentContactPlanPersistenceError",
    "AgentContactPlanProjectNotFoundError",
    "AgentContactPlanProviderError",
    "AgentContactPlanSelectionConsistencyError",
    "AgentContactPlanService",
    "AgentContactPlanWebsiteMissingError",
]

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.company.repository import CompanyRepository
from app.modules.company_enrichment.repository import CompanyEnrichmentRepository
from app.modules.contact_discovery.normalization import normalize_discovered_email
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository
from app.modules.task.models import TaskLifecycleStatus
from app.modules.task.repository import TaskRepository

_INVALID = "Company-scoped outreach completion data is invalid."
_PROJECT_NOT_FOUND = "Project was not found."
_COMPANY_NOT_FOUND = "Company was not found."
_EMAIL_NOT_FOUND = "Trusted company email was not found."
_EMAIL_CONFLICT = "Trusted company email does not match persisted company data."
_CONFLICT = "Company-scoped outreach completion found conflicting state."
_PERSISTENCE = "Company-scoped outreach completion could not be persisted."

_LEAD_SOURCE = "COMPANY_SCOPED_OUTREACH"
_TASK_TITLE = "Prepare personalized company outreach email"
_TASK_DESCRIPTION = "Prepare a personalized manual outreach email for the company recipient."
_ACTIVE_TASK_STATUSES = frozenset(
    (TaskLifecycleStatus.TODO.value, TaskLifecycleStatus.IN_PROGRESS.value)
)


class CompanyScopedOutreachCompletionError(ValueError):
    pass


class CompanyScopedOutreachCompletionInvalidDataError(CompanyScopedOutreachCompletionError):
    pass


class CompanyScopedOutreachCompletionProjectNotFoundError(CompanyScopedOutreachCompletionError):
    pass


class CompanyScopedOutreachCompletionCompanyNotFoundError(CompanyScopedOutreachCompletionError):
    pass


class CompanyScopedOutreachCompletionEmailNotFoundError(CompanyScopedOutreachCompletionError):
    pass


class CompanyScopedOutreachCompletionConflictError(CompanyScopedOutreachCompletionError):
    pass


class CompanyScopedOutreachCompletionPersistenceError(CompanyScopedOutreachCompletionError):
    pass


@dataclass(frozen=True, slots=True)
class CompanyScopedOutreachCompletionInput:
    project_id: int
    company_id: int
    trusted_recipient_email: str


@dataclass(frozen=True, slots=True)
class CompanyScopedOutreachCompletionResult:
    project_id: int
    company_id: int
    lead_id: int
    task_id: int
    lead_created: bool
    lead_reused: bool
    task_created: bool
    task_reused: bool
    contact_id: None = None


class _ProjectRecord(Protocol):
    id: int


class _CompanyRecord(Protocol):
    id: int
    project_id: int


class _EnrichmentRecord(Protocol):
    company_id: int
    email: str | None


class _LeadRecord(Protocol):
    id: int
    company_id: int
    contact_id: int | None


class _TaskRecord(Protocol):
    id: int
    lead_id: int
    title: str
    description: str | None
    status: str
    due_at: datetime | None


class _ProjectRepository(Protocol):
    def get(self, project_id: int) -> _ProjectRecord | None: ...


class _CompanyRepository(Protocol):
    def get_for_project(self, project_id: int, company_id: int) -> _CompanyRecord | None: ...


class _EnrichmentRepository(Protocol):
    def get_by_company_id(self, company_id: int) -> _EnrichmentRecord | None: ...


class _SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def _project_repository(session: Session) -> _ProjectRepository:
    return ProjectRepository(session)


def _company_repository(session: Session) -> _CompanyRepository:
    return CompanyRepository(session)


def _enrichment_repository(session: Session) -> _EnrichmentRepository:
    return CompanyEnrichmentRepository(session)


def _lead_repository(session: Session) -> LeadRepository:
    return LeadRepository(session)


def _task_repository(session: Session) -> TaskRepository:
    return TaskRepository(session)


@dataclass(frozen=True, slots=True)
class CompanyScopedOutreachCompletionComponents:
    project_repository: Callable[[Session], _ProjectRepository] = _project_repository
    company_repository: Callable[[Session], _CompanyRepository] = _company_repository
    enrichment_repository: Callable[[Session], _EnrichmentRepository] = _enrichment_repository
    lead_repository: Callable[[Session], LeadRepository] = _lead_repository
    task_repository: Callable[[Session], TaskRepository] = _task_repository


def _repository_call[T](operation: Callable[[], T]) -> T:
    conflict = False
    failed = False
    value: T | None = None
    try:
        value = operation()
    except IntegrityError:
        conflict = True
    except Exception:
        failed = True
    if conflict:
        raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)
    if failed:
        raise CompanyScopedOutreachCompletionPersistenceError(_PERSISTENCE)
    return cast(T, value)


def _cleanup(operation: Callable[[], object]) -> None:
    with suppress(BaseException):
        operation()


class CompanyScopedOutreachCompletionService:
    def __init__(
        self,
        *,
        session_factory: _SessionFactory,
        components: CompanyScopedOutreachCompletionComponents | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._components = components or CompanyScopedOutreachCompletionComponents()

    def complete(
        self, data: CompanyScopedOutreachCompletionInput
    ) -> CompanyScopedOutreachCompletionResult:
        normalized_email = self._validate_input(data)
        session = self._open_session()
        failed = False
        try:
            result = self._complete(session, data, normalized_email)
            _repository_call(session.commit)
            return result
        except BaseException:
            failed = True
            _cleanup(session.rollback)
            raise
        finally:
            _cleanup(session.close) if failed else session.close()

    def _complete(
        self,
        session: Session,
        data: CompanyScopedOutreachCompletionInput,
        normalized_email: str,
    ) -> CompanyScopedOutreachCompletionResult:
        project_repository = _repository_call(lambda: self._components.project_repository(session))
        company_repository = _repository_call(lambda: self._components.company_repository(session))
        enrichment_repository = _repository_call(
            lambda: self._components.enrichment_repository(session)
        )
        lead_repository = _repository_call(lambda: self._components.lead_repository(session))
        task_repository = _repository_call(lambda: self._components.task_repository(session))

        project = _repository_call(lambda: project_repository.get(data.project_id))
        if project is None:
            raise CompanyScopedOutreachCompletionProjectNotFoundError(_PROJECT_NOT_FOUND)
        if type(project.id) is not int or project.id != data.project_id:
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)

        company = _repository_call(
            lambda: company_repository.get_for_project(data.project_id, data.company_id)
        )
        if company is None:
            raise CompanyScopedOutreachCompletionCompanyNotFoundError(_COMPANY_NOT_FOUND)
        if (
            type(company.id) is not int
            or company.id != data.company_id
            or type(company.project_id) is not int
            or company.project_id != data.project_id
        ):
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)

        enrichment = _repository_call(
            lambda: enrichment_repository.get_by_company_id(data.company_id)
        )
        if enrichment is None or enrichment.email is None:
            raise CompanyScopedOutreachCompletionEmailNotFoundError(_EMAIL_NOT_FOUND)
        if type(enrichment.company_id) is not int or enrichment.company_id != data.company_id:
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)
        persisted_email = self._normalize_persisted_email(enrichment.email)
        if persisted_email != normalized_email:
            raise CompanyScopedOutreachCompletionConflictError(_EMAIL_CONFLICT)

        lead, lead_created = self._find_or_create_lead(lead_repository, data.company_id)
        task, task_created = self._find_or_create_task(task_repository, lead.id)
        return CompanyScopedOutreachCompletionResult(
            project_id=data.project_id,
            company_id=data.company_id,
            lead_id=lead.id,
            task_id=task.id,
            lead_created=lead_created,
            lead_reused=not lead_created,
            task_created=task_created,
            task_reused=not task_created,
        )

    @staticmethod
    def _validate_input(data: object) -> str:
        if (
            type(data) is not CompanyScopedOutreachCompletionInput
            or type(data.project_id) is not int
            or data.project_id <= 0
            or type(data.company_id) is not int
            or data.company_id <= 0
            or type(data.trusted_recipient_email) is not str
        ):
            raise CompanyScopedOutreachCompletionInvalidDataError(_INVALID)
        normalization_failed = False
        normalized: str | None = None
        try:
            normalized = normalize_discovered_email(data.trusted_recipient_email)
        except (TypeError, ValueError):
            normalization_failed = True
        if normalization_failed or normalized is None:
            raise CompanyScopedOutreachCompletionInvalidDataError(_INVALID)
        return normalized

    @staticmethod
    def _normalize_persisted_email(value: object) -> str:
        if type(value) is not str:
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)
        normalization_failed = False
        normalized: str | None = None
        try:
            normalized = normalize_discovered_email(value)
        except (TypeError, ValueError):
            normalization_failed = True
        if normalization_failed or normalized is None:
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)
        return normalized

    def _open_session(self) -> Session:
        failed = False
        session: Session | None = None
        try:
            session = self._session_factory()
        except Exception:
            failed = True
        if failed or session is None:
            raise CompanyScopedOutreachCompletionPersistenceError(_PERSISTENCE)
        return session

    @staticmethod
    def _find_or_create_lead(
        repository: LeadRepository, company_id: int
    ) -> tuple[_LeadRecord, bool]:
        leads = _repository_call(lambda: repository.get_by_company(company_id))
        company_scoped = sorted(
            (lead for lead in leads if getattr(lead, "contact_id", object()) is None),
            key=lambda lead: getattr(lead, "id", 0),
        )
        if company_scoped:
            lead = company_scoped[0]
            CompanyScopedOutreachCompletionService._validate_lead(lead, company_id)
            return lead, False
        lead = _repository_call(
            lambda: repository.create_pending(
                company_id=company_id,
                contact_id=None,
                status="NEW",
                source=_LEAD_SOURCE,
                notes=None,
            )
        )
        CompanyScopedOutreachCompletionService._validate_lead(lead, company_id)
        return lead, True

    @staticmethod
    def _find_or_create_task(repository: TaskRepository, lead_id: int) -> tuple[_TaskRecord, bool]:
        tasks = _repository_call(lambda: repository.get_by_lead(lead_id))
        matching = sorted(
            (
                task
                for task in tasks
                if getattr(task, "title", None) == _TASK_TITLE
                and getattr(task, "status", None) in _ACTIVE_TASK_STATUSES
            ),
            key=lambda task: getattr(task, "id", 0),
        )
        if matching:
            task = matching[0]
            CompanyScopedOutreachCompletionService._validate_task(task, lead_id)
            return task, False
        task = _repository_call(
            lambda: repository.create_for_lead(
                lead_id=lead_id,
                title=_TASK_TITLE,
                description=_TASK_DESCRIPTION,
            )
        )
        CompanyScopedOutreachCompletionService._validate_task(task, lead_id)
        return task, True

    @staticmethod
    def _validate_lead(lead: _LeadRecord, company_id: int) -> None:
        if (
            type(lead.id) is not int
            or lead.id <= 0
            or type(lead.company_id) is not int
            or lead.company_id != company_id
            or lead.contact_id is not None
        ):
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)

    @staticmethod
    def _validate_task(task: _TaskRecord, lead_id: int) -> None:
        if (
            type(task.id) is not int
            or task.id <= 0
            or type(task.lead_id) is not int
            or task.lead_id != lead_id
            or type(task.title) is not str
            or task.title != _TASK_TITLE
            or task.status not in _ACTIVE_TASK_STATUSES
            or task.due_at is not None
        ):
            raise CompanyScopedOutreachCompletionConflictError(_CONFLICT)


__all__ = [
    "CompanyScopedOutreachCompletionCompanyNotFoundError",
    "CompanyScopedOutreachCompletionComponents",
    "CompanyScopedOutreachCompletionConflictError",
    "CompanyScopedOutreachCompletionEmailNotFoundError",
    "CompanyScopedOutreachCompletionError",
    "CompanyScopedOutreachCompletionInput",
    "CompanyScopedOutreachCompletionInvalidDataError",
    "CompanyScopedOutreachCompletionPersistenceError",
    "CompanyScopedOutreachCompletionProjectNotFoundError",
    "CompanyScopedOutreachCompletionResult",
    "CompanyScopedOutreachCompletionService",
]

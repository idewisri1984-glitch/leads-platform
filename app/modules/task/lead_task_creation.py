from datetime import datetime
from typing import Protocol

from app.modules.task.lead_task_creation_schemas import LeadTaskCreationResult

_INVALID_DATA = "Task creation data is invalid."
_NOT_FOUND = "Lead was not found."
_INCONSISTENT_STATE = "Task creation state is inconsistent."


class LeadTaskCreationError(ValueError):
    pass


class LeadTaskCreationInvalidDataError(LeadTaskCreationError):
    pass


class LeadTaskCreationNotFoundError(LeadTaskCreationError):
    pass


class LeadTaskCreationConsistencyError(LeadTaskCreationError):
    pass


class LeadTaskCreationLeadRecord(Protocol):
    id: int
    company_id: int


class LeadTaskCreationTaskRecord(Protocol):
    id: int
    lead_id: int
    title: str
    description: str | None
    status: str
    due_at: datetime | None


class LeadTaskCreationLeadRepository(Protocol):
    def get_for_company(
        self,
        company_id: int,
        lead_id: int,
    ) -> LeadTaskCreationLeadRecord | None: ...


class LeadTaskCreationTaskRepository(Protocol):
    def create_for_lead(
        self,
        *,
        lead_id: int,
        title: str,
        description: str | None = None,
    ) -> LeadTaskCreationTaskRecord: ...


class LeadTaskCreationService:
    def __init__(
        self,
        lead_repository: LeadTaskCreationLeadRepository,
        task_repository: LeadTaskCreationTaskRepository,
    ) -> None:
        self.lead_repository = lead_repository
        self.task_repository = task_repository

    def create(
        self,
        company_id: int,
        lead_id: int,
        title: str,
        description: str | None = None,
    ) -> LeadTaskCreationResult:
        self._validate_id(company_id)
        self._validate_id(lead_id)
        self._validate_title(title)
        self._validate_description(description)

        lead: LeadTaskCreationLeadRecord | None = None
        lead_error: LeadTaskCreationConsistencyError | None = None
        try:
            lead = self.lead_repository.get_for_company(company_id, lead_id)
        except (TypeError, ValueError):
            lead_error = LeadTaskCreationConsistencyError(_INCONSISTENT_STATE)
        if lead_error is not None:
            raise lead_error from None
        if lead is None:
            raise LeadTaskCreationNotFoundError(_NOT_FOUND)
        self._validate_lead(lead, company_id, lead_id)

        task: LeadTaskCreationTaskRecord | None = None
        task_error: LeadTaskCreationConsistencyError | None = None
        try:
            task = self.task_repository.create_for_lead(
                lead_id=lead_id,
                title=title,
                description=description,
            )
        except (TypeError, ValueError):
            task_error = LeadTaskCreationConsistencyError(_INCONSISTENT_STATE)
        if task_error is not None:
            raise task_error from None
        if task is None:
            raise LeadTaskCreationConsistencyError(_INCONSISTENT_STATE)
        self._validate_task(task, lead_id, title, description)

        return LeadTaskCreationResult(
            task_id=task.id,
            company_id=company_id,
            lead_id=lead_id,
            status="TODO",
        )

    @staticmethod
    def _validate_id(value: object) -> None:
        if not LeadTaskCreationService._is_positive_int(value):
            raise LeadTaskCreationInvalidDataError(_INVALID_DATA)

    @staticmethod
    def _validate_title(value: object) -> None:
        if type(value) is not str or not value.strip() or len(value) > 255:
            raise LeadTaskCreationInvalidDataError(_INVALID_DATA)

    @staticmethod
    def _validate_description(value: object) -> None:
        if value is not None and type(value) is not str:
            raise LeadTaskCreationInvalidDataError(_INVALID_DATA)

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return type(value) is int and value > 0

    @staticmethod
    def _validate_lead(
        lead: LeadTaskCreationLeadRecord,
        company_id: int,
        lead_id: int,
    ) -> None:
        returned_lead_id = getattr(lead, "id", None)
        returned_company_id = getattr(lead, "company_id", None)
        if (
            not LeadTaskCreationService._is_positive_int(returned_lead_id)
            or not LeadTaskCreationService._is_positive_int(returned_company_id)
            or returned_lead_id != lead_id
            or returned_company_id != company_id
        ):
            raise LeadTaskCreationConsistencyError(_INCONSISTENT_STATE)

    @staticmethod
    def _validate_task(
        task: LeadTaskCreationTaskRecord,
        lead_id: int,
        title: str,
        description: str | None,
    ) -> None:
        task_id = getattr(task, "id", None)
        returned_lead_id = getattr(task, "lead_id", None)
        returned_title = getattr(task, "title", None)
        returned_description = getattr(task, "description", None)
        status = getattr(task, "status", None)
        due_at = getattr(task, "due_at", None)
        if (
            not LeadTaskCreationService._is_positive_int(task_id)
            or not LeadTaskCreationService._is_positive_int(returned_lead_id)
            or returned_lead_id != lead_id
            or type(returned_title) is not str
            or returned_title != title
            or (returned_description is not None and type(returned_description) is not str)
            or returned_description != description
            or type(status) is not str
            or status != "TODO"
            or due_at is not None
        ):
            raise LeadTaskCreationConsistencyError(_INCONSISTENT_STATE)

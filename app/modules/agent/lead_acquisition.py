from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.modules.company_discovery.schemas import DiscoveryProviderDiagnostic

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class LeadAcquisitionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL_BUDGET_EXHAUSTED = "PARTIAL_BUDGET_EXHAUSTED"
    PARTIAL_PROVIDER_STOP = "PARTIAL_PROVIDER_STOP"


class LeadAcquisitionExportStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class LeadAcquisitionError(ValueError):
    pass


class LeadAcquisitionProviderStopError(LeadAcquisitionError):
    def __init__(
        self,
        message: str,
        *,
        discovery_call_count: int = 0,
        decision_call_count: int = 0,
        discovery_run_count: int = 0,
        candidate_count: int = 0,
        diagnostic: DiscoveryProviderDiagnostic | None = None,
    ) -> None:
        super().__init__(message)
        self.discovery_call_count = discovery_call_count
        self.decision_call_count = decision_call_count
        self.discovery_run_count = discovery_run_count
        self.candidate_count = candidate_count
        self.diagnostic = diagnostic


class LeadAcquisitionCompanyUnavailableError(LeadAcquisitionError):
    pass


class LeadAcquisitionContactUnavailableError(LeadAcquisitionError):
    def __init__(
        self,
        message: str,
        *,
        discovery_call_count: int = 0,
        decision_call_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.discovery_call_count = discovery_call_count
        self.decision_call_count = decision_call_count


class LeadAcquisitionDraftFailure(LeadAcquisitionError):
    pass


class LeadAcquisitionFailureStage(StrEnum):
    COMPANY_DISCOVERY = "COMPANY_DISCOVERY"
    COMPANY_APPLY = "COMPANY_APPLY"
    CONTACT_DISCOVERY = "CONTACT_DISCOVERY"
    CONTACT_APPLY = "CONTACT_APPLY"
    COMPANY_ENRICHMENT = "COMPANY_ENRICHMENT"
    OUTREACH_COMPLETION = "OUTREACH_COMPLETION"
    DRAFT_GENERATION = "DRAFT_GENERATION"
    RESULT_SERIALIZATION = "RESULT_SERIALIZATION"
    INTERNAL_INVARIANT = "INTERNAL_INVARIANT"
    UNKNOWN_RUNTIME = "UNKNOWN_RUNTIME"


class LeadAcquisitionFailureSubstage(StrEnum):
    QUERY_GENERATION = "QUERY_GENERATION"
    DISCOVERY_REQUEST_BUILD = "DISCOVERY_REQUEST_BUILD"
    PROVIDER_CONSTRUCTION = "PROVIDER_CONSTRUCTION"
    PROVIDER_EXECUTION = "PROVIDER_EXECUTION"
    PROVIDER_RESULT_VALIDATION = "PROVIDER_RESULT_VALIDATION"
    RESULT_NORMALIZATION = "RESULT_NORMALIZATION"
    DISCOVERY_PERSISTENCE = "DISCOVERY_PERSISTENCE"
    CANDIDATE_BINDING = "CANDIDATE_BINDING"
    COMPANY_DECISION = "COMPANY_DECISION"
    DECISION_RESULT_VALIDATION = "DECISION_RESULT_VALIDATION"
    SELECTION_BINDING = "SELECTION_BINDING"
    COMPANY_PLAN_RESULT_BUILD = "COMPANY_PLAN_RESULT_BUILD"
    UNKNOWN_COMPANY_PLAN = "UNKNOWN_COMPANY_PLAN"


class LeadAcquisitionCompanyPlanSubstageError(LeadAcquisitionError):
    def __init__(self, substage: LeadAcquisitionFailureSubstage) -> None:
        self.substage = substage
        super().__init__("Agent company plan failed.")


_FAILURE_STAGE_LABELS = {
    LeadAcquisitionFailureStage.COMPANY_DISCOVERY: "company discovery",
    LeadAcquisitionFailureStage.COMPANY_APPLY: "company apply",
    LeadAcquisitionFailureStage.CONTACT_DISCOVERY: "contact discovery",
    LeadAcquisitionFailureStage.CONTACT_APPLY: "contact apply",
    LeadAcquisitionFailureStage.COMPANY_ENRICHMENT: "company enrichment",
    LeadAcquisitionFailureStage.OUTREACH_COMPLETION: "outreach completion",
    LeadAcquisitionFailureStage.DRAFT_GENERATION: "draft generation",
    LeadAcquisitionFailureStage.RESULT_SERIALIZATION: "result serialization",
    LeadAcquisitionFailureStage.INTERNAL_INVARIANT: "an internal invariant check",
    LeadAcquisitionFailureStage.UNKNOWN_RUNTIME: "execution",
}


class LeadAcquisitionExecutionError(LeadAcquisitionError):
    category = "execution_error"

    def __init__(
        self,
        failure_stage: LeadAcquisitionFailureStage,
        failure_substage: LeadAcquisitionFailureSubstage | None = None,
    ) -> None:
        self.failure_stage = failure_stage
        self.failure_substage = failure_substage
        super().__init__(
            f"Agent lead acquisition failed during {_FAILURE_STAGE_LABELS[failure_stage]}."
        )


def _execution_error(
    stage: LeadAcquisitionFailureStage,
    substage: LeadAcquisitionFailureSubstage | None = None,
) -> LeadAcquisitionExecutionError:
    return LeadAcquisitionExecutionError(stage, substage)


class LeadAcquisitionInput(BaseModel):
    model_config = _STRICT

    project_id: int
    search_profile_id: int
    limit: int
    goal: str
    export_file: Path | None = None
    overwrite_export: bool = False

    @field_validator("project_id", "search_profile_id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if type(value) is not int or value <= 0:
            raise ValueError("Identifier is invalid.")
        return value

    @field_validator("limit", mode="before")
    @classmethod
    def validate_limit(cls, value: object) -> object:
        if type(value) is not int or not 1 <= value <= 50:
            raise ValueError("Limit is invalid.")
        return value

    @field_validator("goal", mode="before")
    @classmethod
    def validate_goal(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Goal is invalid.")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 1000:
            raise ValueError("Goal is invalid.")
        try:
            normalized.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Goal is invalid.") from None
        return normalized

    @model_validator(mode="after")
    def validate_export(self) -> LeadAcquisitionInput:
        if self.overwrite_export and self.export_file is None:
            raise ValueError("Export overwrite requires an export file.")
        return self


class LeadAcquisitionResult(BaseModel):
    model_config = _STRICT

    project_id: int
    search_profile_id: int
    requested_limit: int
    completed_count: int
    person_scoped_count: int
    company_scoped_count: int
    companies_created: int
    companies_reused: int
    contacts_created: int
    contacts_reused: int
    leads_created: int
    leads_reused: int
    tasks_created: int
    tasks_reused: int
    drafts_created: int
    drafts_reused: int
    duplicates_skipped: int
    no_selection_count: int
    no_contact_count: int
    no_email_count: int
    draft_failure_count: int
    attempt_count: int
    attempt_budget: int
    budget_exhausted: bool
    discovery_run_count: int
    candidate_count: int
    completed_company_ids: tuple[int, ...]
    completed_contact_ids: tuple[int, ...]
    completed_lead_ids: tuple[int, ...]
    completed_task_ids: tuple[int, ...]
    completed_draft_ids: tuple[int, ...]
    company_discovery_call_count: int
    company_decision_call_count: int
    provider_diagnostic: DiscoveryProviderDiagnostic | None = None
    contact_discovery_call_count: int
    contact_decision_call_count: int
    draft_generation_call_count: int
    export_file: str | None
    export_status: LeadAcquisitionExportStatus
    status: LeadAcquisitionStatus

    @model_validator(mode="after")
    def validate_alignment(self) -> LeadAcquisitionResult:
        counts = (
            self.completed_count,
            self.person_scoped_count,
            self.company_scoped_count,
            self.companies_created,
            self.companies_reused,
            self.contacts_created,
            self.contacts_reused,
            self.leads_created,
            self.leads_reused,
            self.tasks_created,
            self.tasks_reused,
            self.drafts_created,
            self.drafts_reused,
            self.duplicates_skipped,
            self.no_selection_count,
            self.no_contact_count,
            self.no_email_count,
            self.draft_failure_count,
            self.attempt_count,
            self.attempt_budget,
            self.discovery_run_count,
            self.candidate_count,
            self.company_discovery_call_count,
            self.company_decision_call_count,
            self.contact_discovery_call_count,
            self.contact_decision_call_count,
            self.draft_generation_call_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("Acquisition counts are invalid.")
        if self.completed_count != self.person_scoped_count + self.company_scoped_count:
            raise ValueError("Completion counts are inconsistent.")
        if self.completed_count > self.requested_limit or self.attempt_count > self.attempt_budget:
            raise ValueError("Acquisition bounds are inconsistent.")
        if self.budget_exhausted and self.attempt_count != self.attempt_budget:
            raise ValueError("Budget exhaustion is inconsistent.")
        if (
            any(
                len(values) != self.completed_count
                for values in (
                    self.completed_company_ids,
                    self.completed_lead_ids,
                    self.completed_task_ids,
                    self.completed_draft_ids,
                )
            )
            or len(self.completed_contact_ids) != self.person_scoped_count
        ):
            raise ValueError("Completed identifiers are inconsistent.")
        identifier_groups = (
            self.completed_company_ids,
            self.completed_contact_ids,
            self.completed_lead_ids,
            self.completed_task_ids,
            self.completed_draft_ids,
        )
        if any(
            any(type(identifier) is not int or identifier <= 0 for identifier in identifiers)
            or len(set(identifiers)) != len(identifiers)
            for identifiers in identifier_groups
        ):
            raise ValueError("Completed identifiers are invalid.")
        if self.status is LeadAcquisitionStatus.COMPLETE:
            if self.completed_count != self.requested_limit or self.budget_exhausted:
                raise ValueError("Complete acquisition state is inconsistent.")
        elif self.status is LeadAcquisitionStatus.PARTIAL_BUDGET_EXHAUSTED:
            if not self.budget_exhausted or self.completed_count >= self.requested_limit:
                raise ValueError("Budget state is inconsistent.")
        elif self.completed_count >= self.requested_limit or self.budget_exhausted:
            raise ValueError("Provider-stop state is inconsistent.")
        if (self.export_file is None) != (
            self.export_status is LeadAcquisitionExportStatus.NOT_REQUESTED
        ):
            raise ValueError("Export state is inconsistent.")
        return self


@dataclass(frozen=True, slots=True)
class CompanyPlanOutcome:
    discovery_run_id: int
    candidate_count: int
    selected_candidate_id: int | None
    discovery_call_count: int
    decision_call_count: int
    decision_request_fingerprint: str | None = None
    decision_suppressed: bool = False
    existing_company_id: int | None = None
    candidate_ids: tuple[int, ...] = ()
    candidate_domains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompanyApplyOutcome:
    company_id: int
    created: bool
    reused: bool


@dataclass(frozen=True, slots=True)
class ContactPlanOutcome:
    selected_candidate_id: int | None
    selected_email: str | None
    handoff_token: str | None
    discovery_call_count: int
    decision_call_count: int


@dataclass(frozen=True, slots=True)
class ContactApplyOutcome:
    contact_id: int
    lead_id: int
    task_id: int
    contact_created: bool
    contact_reused: bool
    lead_created: bool
    lead_reused: bool
    task_created: bool
    task_reused: bool


@dataclass(frozen=True, slots=True)
class CompanyCompletionOutcome:
    lead_id: int
    task_id: int
    lead_created: bool
    lead_reused: bool
    task_created: bool
    task_reused: bool


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    draft_id: int
    contact_id: int | None
    lead_id: int
    recipient_email: str
    status: str
    created: bool
    reused: bool


@dataclass(frozen=True, slots=True)
class ExportOutcome:
    output_file: Path


@dataclass(frozen=True, slots=True)
class LeadAcquisitionDependencies:
    company_plan: Callable[[int, int, str], CompanyPlanOutcome]
    company_apply: Callable[[int, int, int], CompanyApplyOutcome]
    contact_plan: Callable[[int, int, str], ContactPlanOutcome]
    contact_apply: Callable[[int, int, int, str, str], ContactApplyOutcome]
    company_email: Callable[[int], str | None]
    company_complete: Callable[[int, int, str], CompanyCompletionOutcome]
    draft_generate: Callable[[int, int, int | None, int, int, str], DraftOutcome]
    export_crm: Callable[[int, Path, bool], ExportOutcome]
    company_plan_attempt: (
        Callable[[int, int, str, int, tuple[str, ...]], CompanyPlanOutcome] | None
    ) = None
    company_already_complete: Callable[[int, int], bool] | None = None
    company_enrich: Callable[[int], object] | None = None


@dataclass(slots=True)
class _State:
    completed_count: int = 0
    person_scoped_count: int = 0
    company_scoped_count: int = 0
    companies_created: int = 0
    companies_reused: int = 0
    contacts_created: int = 0
    contacts_reused: int = 0
    leads_created: int = 0
    leads_reused: int = 0
    tasks_created: int = 0
    tasks_reused: int = 0
    drafts_created: int = 0
    drafts_reused: int = 0
    duplicates_skipped: int = 0
    no_selection_count: int = 0
    no_contact_count: int = 0
    no_email_count: int = 0
    draft_failure_count: int = 0
    attempt_count: int = 0
    discovery_run_count: int = 0
    candidate_count: int = 0
    company_discovery_call_count: int = 0
    company_decision_call_count: int = 0
    provider_diagnostic: DiscoveryProviderDiagnostic | None = None
    contact_discovery_call_count: int = 0
    contact_decision_call_count: int = 0
    draft_generation_call_count: int = 0
    completed_company_ids: list[int] = field(default_factory=list)
    completed_contact_ids: list[int] = field(default_factory=list)
    completed_lead_ids: list[int] = field(default_factory=list)
    completed_task_ids: list[int] = field(default_factory=list)
    completed_draft_ids: list[int] = field(default_factory=list)
    query_template_cursor: int = 0
    seen_candidate_ids: set[int] = field(default_factory=set)
    seen_candidate_domains: set[str] = field(default_factory=set)
    seen_request_fingerprints: set[str] = field(default_factory=set)


def _usable_email(value: str | None) -> str | None:
    if value is None or type(value) is not str or any(character.isspace() for character in value):
        return None
    normalized = value.strip().casefold()
    if normalized.count("@") != 1:
        return None
    local, domain = normalized.split("@", 1)
    if (
        not local
        or not domain
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
    ):
        return None
    return normalized


class LeadAcquisitionService:
    def __init__(self, dependencies: LeadAcquisitionDependencies) -> None:
        self._dependencies = dependencies

    def acquire(self, data: LeadAcquisitionInput) -> LeadAcquisitionResult:
        if type(data) is not LeadAcquisitionInput:
            raise LeadAcquisitionError("Lead acquisition data is invalid.")
        attempt_budget = min(max(data.limit * 5, 10), 100)
        state = _State()
        provider_stopped = False

        while state.completed_count < data.limit and state.attempt_count < attempt_budget:
            state.attempt_count += 1
            runtime_error: LeadAcquisitionExecutionError | None = None
            try:
                if self._dependencies.company_plan_attempt is None:
                    plan = self._dependencies.company_plan(
                        data.project_id, data.search_profile_id, data.goal
                    )
                else:
                    plan = self._dependencies.company_plan_attempt(
                        data.project_id,
                        data.search_profile_id,
                        data.goal,
                        state.query_template_cursor,
                        tuple(sorted(state.seen_request_fingerprints)),
                    )
                state.query_template_cursor += 1
            except LeadAcquisitionProviderStopError as error:
                state.company_discovery_call_count += error.discovery_call_count
                state.company_decision_call_count += error.decision_call_count
                state.discovery_run_count += error.discovery_run_count
                state.candidate_count += error.candidate_count
                state.provider_diagnostic = error.diagnostic
                provider_stopped = True
                break
            except LeadAcquisitionCompanyPlanSubstageError as error:
                runtime_error = _execution_error(
                    LeadAcquisitionFailureStage.COMPANY_DISCOVERY,
                    error.substage,
                )
            except ValueError:
                runtime_error = _execution_error(
                    LeadAcquisitionFailureStage.COMPANY_DISCOVERY,
                    LeadAcquisitionFailureSubstage.UNKNOWN_COMPANY_PLAN,
                )
            if runtime_error is not None:
                raise runtime_error
            state.company_discovery_call_count += plan.discovery_call_count
            state.company_decision_call_count += plan.decision_call_count
            state.discovery_run_count += 1
            state.candidate_count += plan.candidate_count
            state.seen_candidate_ids.update(plan.candidate_ids)
            state.seen_candidate_domains.update(
                domain.casefold() for domain in plan.candidate_domains if domain
            )
            if plan.decision_request_fingerprint is not None:
                state.seen_request_fingerprints.add(plan.decision_request_fingerprint)
            if plan.decision_suppressed:
                state.duplicates_skipped += 1
                continue
            if plan.selected_candidate_id is None:
                state.no_selection_count += 1
                continue

            if plan.existing_company_id is not None:
                company = CompanyApplyOutcome(plan.existing_company_id, False, True)
                if (
                    self._dependencies.company_already_complete is not None
                    and self._dependencies.company_already_complete(
                        data.project_id, company.company_id
                    )
                ):
                    state.drafts_reused += 1
                    state.duplicates_skipped += 1
                    continue
            else:
                runtime_error = None
                try:
                    company = self._dependencies.company_apply(
                        data.project_id, plan.discovery_run_id, plan.selected_candidate_id
                    )
                except LeadAcquisitionCompanyUnavailableError:
                    state.no_selection_count += 1
                    continue
                except ValueError:
                    runtime_error = _execution_error(LeadAcquisitionFailureStage.COMPANY_APPLY)
                if runtime_error is not None:
                    raise runtime_error
            _require_exclusive(company.created, company.reused)
            state.companies_created += int(company.created)
            state.companies_reused += int(company.reused)

            contact: ContactApplyOutcome | None = None
            runtime_error = None
            try:
                contact_plan = self._dependencies.contact_plan(
                    data.project_id, company.company_id, data.goal
                )
                state.contact_discovery_call_count += contact_plan.discovery_call_count
                state.contact_decision_call_count += contact_plan.decision_call_count
            except LeadAcquisitionContactUnavailableError as error:
                state.contact_discovery_call_count += error.discovery_call_count
                state.contact_decision_call_count += error.decision_call_count
                contact_plan = ContactPlanOutcome(None, None, None, 0, 0)
            except ValueError:
                runtime_error = _execution_error(LeadAcquisitionFailureStage.CONTACT_DISCOVERY)
            if runtime_error is not None:
                raise runtime_error

            selected_email = _usable_email(contact_plan.selected_email)
            if (
                contact_plan.selected_candidate_id is not None
                and contact_plan.handoff_token is not None
            ):
                runtime_error = None
                try:
                    contact = self._dependencies.contact_apply(
                        data.project_id,
                        company.company_id,
                        contact_plan.selected_candidate_id,
                        data.goal,
                        contact_plan.handoff_token,
                    )
                except LeadAcquisitionContactUnavailableError:
                    contact = None
                except ValueError:
                    runtime_error = _execution_error(LeadAcquisitionFailureStage.CONTACT_APPLY)
                if runtime_error is not None:
                    raise runtime_error

            if contact is not None:
                _require_exclusive(contact.contact_created, contact.contact_reused)
                _require_exclusive(contact.lead_created, contact.lead_reused)
                _require_exclusive(contact.task_created, contact.task_reused)
                state.contacts_created += int(contact.contact_created)
                state.contacts_reused += int(contact.contact_reused)
                state.leads_created += int(contact.lead_created)
                state.leads_reused += int(contact.lead_reused)
                state.tasks_created += int(contact.task_created)
                state.tasks_reused += int(contact.task_reused)
                if selected_email is not None:
                    self._complete_draft(
                        data,
                        state,
                        company.company_id,
                        contact.contact_id,
                        contact.lead_id,
                        contact.task_id,
                        selected_email,
                    )
                    continue
            else:
                state.no_contact_count += 1

            runtime_error = None
            try:
                company_email = _usable_email(self._dependencies.company_email(company.company_id))
                if company_email is None and self._dependencies.company_enrich is not None:
                    self._dependencies.company_enrich(company.company_id)
                    company_email = _usable_email(
                        self._dependencies.company_email(company.company_id)
                    )
            except ValueError:
                runtime_error = _execution_error(LeadAcquisitionFailureStage.COMPANY_ENRICHMENT)
            if runtime_error is not None:
                raise runtime_error
            if company_email is None:
                state.no_email_count += 1
                continue
            runtime_error = None
            try:
                completion = self._dependencies.company_complete(
                    data.project_id, company.company_id, company_email
                )
            except ValueError:
                runtime_error = _execution_error(LeadAcquisitionFailureStage.OUTREACH_COMPLETION)
            if runtime_error is not None:
                raise runtime_error
            _require_exclusive(completion.lead_created, completion.lead_reused)
            _require_exclusive(completion.task_created, completion.task_reused)
            state.leads_created += int(completion.lead_created)
            state.leads_reused += int(completion.lead_reused)
            state.tasks_created += int(completion.task_created)
            state.tasks_reused += int(completion.task_reused)
            self._complete_draft(
                data,
                state,
                company.company_id,
                None,
                completion.lead_id,
                completion.task_id,
                company_email,
            )

        budget_exhausted = (
            state.completed_count < data.limit and state.attempt_count == attempt_budget
        )
        if state.completed_count == data.limit:
            status = LeadAcquisitionStatus.COMPLETE
        elif provider_stopped:
            status = LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
        else:
            status = LeadAcquisitionStatus.PARTIAL_BUDGET_EXHAUSTED

        export_status = LeadAcquisitionExportStatus.NOT_REQUESTED
        export_file: str | None = None
        if data.export_file is not None:
            export_file = str(data.export_file)
            try:
                exported = self._dependencies.export_crm(
                    data.project_id, data.export_file, data.overwrite_export
                )
                export_file = str(exported.output_file)
                export_status = LeadAcquisitionExportStatus.SUCCEEDED
            except Exception:
                export_status = LeadAcquisitionExportStatus.FAILED

        runtime_error = None
        try:
            result = LeadAcquisitionResult(
                project_id=data.project_id,
                search_profile_id=data.search_profile_id,
                requested_limit=data.limit,
                completed_count=state.completed_count,
                person_scoped_count=state.person_scoped_count,
                company_scoped_count=state.company_scoped_count,
                companies_created=state.companies_created,
                companies_reused=state.companies_reused,
                contacts_created=state.contacts_created,
                contacts_reused=state.contacts_reused,
                leads_created=state.leads_created,
                leads_reused=state.leads_reused,
                tasks_created=state.tasks_created,
                tasks_reused=state.tasks_reused,
                drafts_created=state.drafts_created,
                drafts_reused=state.drafts_reused,
                duplicates_skipped=state.duplicates_skipped,
                no_selection_count=state.no_selection_count,
                no_contact_count=state.no_contact_count,
                no_email_count=state.no_email_count,
                draft_failure_count=state.draft_failure_count,
                attempt_count=state.attempt_count,
                attempt_budget=attempt_budget,
                budget_exhausted=budget_exhausted,
                discovery_run_count=state.discovery_run_count,
                candidate_count=state.candidate_count,
                completed_company_ids=tuple(state.completed_company_ids),
                completed_contact_ids=tuple(state.completed_contact_ids),
                completed_lead_ids=tuple(state.completed_lead_ids),
                completed_task_ids=tuple(state.completed_task_ids),
                completed_draft_ids=tuple(state.completed_draft_ids),
                company_discovery_call_count=state.company_discovery_call_count,
                company_decision_call_count=state.company_decision_call_count,
                provider_diagnostic=state.provider_diagnostic,
                contact_discovery_call_count=state.contact_discovery_call_count,
                contact_decision_call_count=state.contact_decision_call_count,
                draft_generation_call_count=state.draft_generation_call_count,
                export_file=export_file,
                export_status=export_status,
                status=status,
            )
        except (ValidationError, ValueError):
            runtime_error = _execution_error(LeadAcquisitionFailureStage.RESULT_SERIALIZATION)
        if runtime_error is not None:
            raise runtime_error
        return result

    def _complete_draft(
        self,
        data: LeadAcquisitionInput,
        state: _State,
        company_id: int,
        contact_id: int | None,
        lead_id: int,
        task_id: int,
        expected_email: str,
    ) -> None:
        state.draft_generation_call_count += 1
        runtime_error: LeadAcquisitionExecutionError | None = None
        try:
            draft = self._dependencies.draft_generate(
                data.project_id, company_id, contact_id, lead_id, task_id, data.goal
            )
        except LeadAcquisitionDraftFailure:
            state.draft_failure_count += 1
            return
        except ValueError:
            runtime_error = _execution_error(LeadAcquisitionFailureStage.DRAFT_GENERATION)
        if runtime_error is not None:
            raise runtime_error
        if (
            draft.status != "DRAFT"
            or draft.contact_id != contact_id
            or draft.lead_id != lead_id
            or _usable_email(draft.recipient_email) != expected_email
        ):
            state.draft_failure_count += 1
            return
        _require_exclusive(draft.created, draft.reused)
        state.drafts_created += int(draft.created)
        state.drafts_reused += int(draft.reused)
        if draft.reused:
            state.duplicates_skipped += 1
            return
        state.completed_count += 1
        state.completed_company_ids.append(company_id)
        state.completed_lead_ids.append(lead_id)
        state.completed_task_ids.append(task_id)
        state.completed_draft_ids.append(draft.draft_id)
        if contact_id is None:
            state.company_scoped_count += 1
        else:
            state.person_scoped_count += 1
            state.completed_contact_ids.append(contact_id)


def _require_exclusive(created: bool, reused: bool) -> None:
    if type(created) is not bool or type(reused) is not bool or created == reused:
        raise LeadAcquisitionError("Materialization outcome is inconsistent.")


__all__ = [
    "CompanyApplyOutcome",
    "CompanyCompletionOutcome",
    "CompanyPlanOutcome",
    "ContactApplyOutcome",
    "ContactPlanOutcome",
    "DraftOutcome",
    "ExportOutcome",
    "LeadAcquisitionCompanyUnavailableError",
    "LeadAcquisitionContactUnavailableError",
    "LeadAcquisitionDependencies",
    "LeadAcquisitionDraftFailure",
    "LeadAcquisitionExecutionError",
    "LeadAcquisitionFailureStage",
    "LeadAcquisitionError",
    "LeadAcquisitionExportStatus",
    "LeadAcquisitionInput",
    "LeadAcquisitionProviderStopError",
    "LeadAcquisitionResult",
    "LeadAcquisitionService",
    "LeadAcquisitionStatus",
]

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Never, Protocol

import typer
from pydantic import ValidationError
from sqlalchemy.orm import Session
from typer import _click as click
from typer._click.exceptions import UsageError
from typer.core import TyperCommand

from app.cli._lazy_dependencies import SessionLocal
from app.cli.email_draft import app as email_draft_app
from app.modules.agent.company_apply import (
    AgentCompanyApplyConfirmationRequiredError,
    AgentCompanyApplyConflictError,
    AgentCompanyApplyConsistencyError,
    AgentCompanyApplyError,
    AgentCompanyApplyInternalError,
    AgentCompanyApplyInvalidDataError,
    AgentCompanyApplyNotEligibleError,
    AgentCompanyApplyNotFoundError,
    AgentCompanyApplyPersistenceError,
    AgentCompanyApplyService,
    AgentCompanyApplyStaleHandoffError,
)
from app.modules.agent.company_apply_schemas import (
    AgentCompanyApplyInput,
    AgentCompanyApplyResult,
)
from app.modules.agent.contact_apply import (
    AgentContactApplyConfirmationRequiredError,
    AgentContactApplyConflictError,
    AgentContactApplyConsistencyError,
    AgentContactApplyError,
    AgentContactApplyInternalError,
    AgentContactApplyInvalidDataError,
    AgentContactApplyNotEligibleError,
    AgentContactApplyNotFoundError,
    AgentContactApplyPersistenceError,
    AgentContactApplyService,
    AgentContactApplyStaleHandoffError,
)
from app.modules.agent.contact_apply_schemas import (
    AgentContactApplyInput,
    AgentContactApplyResult,
)
from app.modules.agent.execution import (
    CompanyApplyComponents,
    CompanyPlanComponents,
    ContactApplyComponents,
    ContactPlanComponents,
    execute_company_apply,
    execute_company_plan,
    execute_contact_apply,
    execute_contact_plan,
)
from app.modules.agent.execution import (
    _LazyDecisionFactory as _LazyDecisionFactory,
)
from app.modules.company.repository import CompanyRepository
from app.modules.company_discovery.candidate_promotion import (
    CompanyDiscoveryCandidatePromotionService,
)
from app.modules.company_discovery.candidate_review import CompanyDiscoveryCandidateReviewService
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryStagingService,
)
from app.modules.company_discovery.staging_repository import (
    CompanyDiscoveryStagingRepository,
)
from app.modules.contact.repository import ContactRepository
from app.modules.contact_discovery.candidate_promotion import (
    ContactDiscoveryCandidatePromotionService,
)
from app.modules.contact_discovery.candidate_review import ContactDiscoveryCandidateReviewService
from app.modules.contact_discovery.repository import ContactDiscoveryRepository
from app.modules.lead.repository import LeadRepository
from app.modules.project import ProjectRepository
from app.modules.search_profile import (
    SearchProfileQueryGenerator,
    SearchProfileRepository,
    SearchProfileService,
)
from app.modules.task.repository import TaskRepository

if TYPE_CHECKING:
    from app.modules.agent.company_plan import (
        AgentCompanyPlanBindingError,
        AgentCompanyPlanDecisionError,
        AgentCompanyPlanDiscoveryDataError,
        AgentCompanyPlanError,
        AgentCompanyPlanInternalError,
        AgentCompanyPlanInvalidDataError,
        AgentCompanyPlanPersistenceError,
        AgentCompanyPlanProjectNotFoundError,
        AgentCompanyPlanSearchProfileNotFoundError,
        AgentCompanyPlanSearchProfileNotReadyError,
        AgentCompanyPlanSearchProviderError,
        AgentCompanyPlanSelectionError,
        AgentCompanyPlanService,
        DecisionBoundary,
    )
    from app.modules.agent.company_plan_schemas import (
        AgentCompanyPlanInput,
        AgentCompanyPlanResult,
    )
    from app.modules.agent.company_selection import (
        AgentCompanySelectionService,
    )
    from app.modules.agent.contact_plan import (
        AgentContactPlanBindingMismatchError,
        AgentContactPlanCompanyNotFoundError,
        AgentContactPlanDiscoveryResultError,
        AgentContactPlanError,
        AgentContactPlanInternalError,
        AgentContactPlanInvalidDataError,
        AgentContactPlanPersistenceError,
        AgentContactPlanProjectNotFoundError,
        AgentContactPlanProviderError,
        AgentContactPlanSelectionConsistencyError,
        AgentContactPlanService,
        AgentContactPlanWebsiteMissingError,
    )
    from app.modules.agent.contact_plan_schemas import (
        AgentContactPlanInput,
        AgentContactPlanResult,
    )
    from app.modules.agent.lead_acquisition import (
        LeadAcquisitionError,
        LeadAcquisitionExecutionError,
        LeadAcquisitionFailureStage,
        LeadAcquisitionInput,
        LeadAcquisitionResult,
    )
    from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
    from app.modules.contact_discovery.service import ContactDiscoveryProvider
    from app.providers.openai_decision import OpenAIDecisionClient
    from app.providers.serpapi import SerpApiClient

app = typer.Typer(help="Bounded Agent planning commands.")
company_select_app = typer.Typer(help="Company-selection planning commands.")
app.add_typer(company_select_app, name="company-select")
contact_select_app = typer.Typer(help="Contact-selection planning commands.")
app.add_typer(contact_select_app, name="contact-select")
app.add_typer(email_draft_app, name="email-draft")

_ACQUIRE_INVALID = "Agent lead acquisition data is invalid."
_ACQUIRE_INTERNAL = "Agent lead acquisition failed."
_ACQUIRE_OPTIONS = (
    "--project-id",
    "--search-profile-id",
    "--limit",
    "--goal",
    "--export-excel",
    "--overwrite-export",
    "--output",
)


def _load_lead_acquisition_dependencies() -> None:
    from app.modules.agent import lead_acquisition

    namespace = globals()
    for name in (
        "LeadAcquisitionError",
        "LeadAcquisitionExecutionError",
        "LeadAcquisitionFailureStage",
        "LeadAcquisitionInput",
        "LeadAcquisitionResult",
    ):
        namespace.setdefault(name, getattr(lead_acquisition, name))


class _AcquireLeadsCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(arg in {"--help", "-h"} for arg in args):
            for option in _ACQUIRE_OPTIONS:
                if sum(arg == option or arg.startswith(f"{option}=") for arg in args) > 1:
                    self._invalid_input()
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            self._invalid_input()

    @staticmethod
    def _invalid_input() -> Never:
        click.echo(_ACQUIRE_INVALID, err=True)
        raise click.exceptions.Exit(2)


def _acquire_integer(value: str, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(_ACQUIRE_INVALID) from None
    if parsed <= 0 or value.strip() != value or (maximum is not None and parsed > maximum):
        raise ValueError(_ACQUIRE_INVALID)
    return parsed


def execute_agent_lead_acquisition(data: LeadAcquisitionInput) -> LeadAcquisitionResult:
    from app.modules.lead_acquisition_execution import execute_lead_acquisition

    return execute_lead_acquisition(data, session_factory=SessionLocal)


def render_agent_lead_acquisition(result: LeadAcquisitionResult, output: str) -> str:
    _load_lead_acquisition_dependencies()
    if type(result) is not LeadAcquisitionResult or output not in {"text", "json"}:
        raise ValueError(_ACQUIRE_INTERNAL)
    try:
        validated = LeadAcquisitionResult(**result.model_dump())
        values = validated.model_dump(mode="json")
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise ValueError(_ACQUIRE_INTERNAL) from None
    if output == "json":
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    labels = (
        ("Requested", "requested_limit"),
        ("Completed", "completed_count"),
        ("Person scoped", "person_scoped_count"),
        ("Company scoped", "company_scoped_count"),
        ("Companies created", "companies_created"),
        ("Companies reused", "companies_reused"),
        ("Contacts created", "contacts_created"),
        ("Contacts reused", "contacts_reused"),
        ("Leads created", "leads_created"),
        ("Leads reused", "leads_reused"),
        ("Tasks created", "tasks_created"),
        ("Tasks reused", "tasks_reused"),
        ("Drafts created", "drafts_created"),
        ("Drafts reused", "drafts_reused"),
        ("Duplicates", "duplicates_skipped"),
        ("No selection", "no_selection_count"),
        ("No contact", "no_contact_count"),
        ("No email", "no_email_count"),
        ("Draft failures", "draft_failure_count"),
    )
    lines = [f"{label}: {values[field]}" for label, field in labels]
    lines.extend(
        (
            f"Attempts / budget: {values['attempt_count']} / {values['attempt_budget']}",
            f"Export result: {values['export_status']}",
            f"Status: {values['status']}",
        )
    )
    return "\n".join(lines)


def _lead_acquisition_error_payload(
    error: LeadAcquisitionExecutionError,
) -> dict[str, object]:
    return {
        "status": "ERROR",
        "error_category": error.category,
        "failure_stage": error.failure_stage.value,
        "failure_substage": (
            error.failure_substage.value if error.failure_substage is not None else None
        ),
        "message": str(error),
    }


def _raise_lead_acquisition_execution_error(
    error: LeadAcquisitionExecutionError,
    output: str,
) -> Never:
    if output == "json":
        typer.echo(
            json.dumps(
                _lead_acquisition_error_payload(error),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        typer.echo(str(error), err=True)
    raise typer.Exit(1)


@app.command("acquire-leads", cls=_AcquireLeadsCommand)
def acquire_leads(
    project_id: Annotated[str, typer.Option("--project-id", metavar="INTEGER")],
    search_profile_id: Annotated[str, typer.Option("--search-profile-id", metavar="INTEGER")],
    limit: Annotated[str, typer.Option("--limit", metavar="INTEGER")],
    goal: Annotated[str, typer.Option("--goal")],
    export_excel: Annotated[str | None, typer.Option("--export-excel", metavar="PATH")] = None,
    overwrite_export: Annotated[
        bool,
        typer.Option(
            "--overwrite-export",
            help="Allow an existing CRM Excel export file to be replaced.",
        ),
    ] = False,
    output: Annotated[str, typer.Option("--output", help="Output format: text or json.")] = "text",
) -> None:
    _load_lead_acquisition_dependencies()
    try:
        if output not in {"text", "json"}:
            raise ValueError(_ACQUIRE_INVALID)
        from pathlib import Path

        data = LeadAcquisitionInput(
            project_id=_acquire_integer(project_id),
            search_profile_id=_acquire_integer(search_profile_id),
            limit=_acquire_integer(limit, maximum=50),
            goal=goal,
            export_file=None if export_excel is None else Path(export_excel),
            overwrite_export=overwrite_export,
        )
    except (ValidationError, ValueError):
        typer.echo(_ACQUIRE_INVALID, err=True)
        raise typer.Exit(2) from None

    runtime_error: LeadAcquisitionExecutionError | None = None
    try:
        result = execute_agent_lead_acquisition(data)
    except LeadAcquisitionExecutionError as error:
        runtime_error = error
    except LeadAcquisitionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except ValueError:
        runtime_error = LeadAcquisitionExecutionError(LeadAcquisitionFailureStage.UNKNOWN_RUNTIME)
    except Exception:
        typer.echo(_ACQUIRE_INTERNAL, err=True)
        raise typer.Exit(1) from None
    if runtime_error is not None:
        _raise_lead_acquisition_execution_error(runtime_error, output)

    runtime_error = None
    try:
        rendered = render_agent_lead_acquisition(result, output)
    except (ValidationError, ValueError):
        runtime_error = LeadAcquisitionExecutionError(
            LeadAcquisitionFailureStage.RESULT_SERIALIZATION
        )
    except Exception:
        typer.echo(_ACQUIRE_INTERNAL, err=True)
        raise typer.Exit(1) from None
    if runtime_error is not None:
        _raise_lead_acquisition_execution_error(runtime_error, output)
    typer.echo(rendered)


_CONTACT_INVALID = "Agent contact plan data is invalid."
_CONTACT_INTERNAL = "Agent contact plan failed."
_CONTACT_OPTIONS = ("--project-id", "--company-id", "--goal", "--output")


def _load_contact_plan_dependencies() -> None:
    from app.modules.agent import contact_plan, contact_plan_schemas

    namespace = globals()
    for name in (
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
    ):
        namespace.setdefault(name, getattr(contact_plan, name))
    for name in ("AgentContactPlanInput", "AgentContactPlanResult"):
        namespace.setdefault(name, getattr(contact_plan_schemas, name))


def _contact_plan_error_codes() -> tuple[tuple[type[AgentContactPlanError], int], ...]:
    _load_contact_plan_dependencies()
    return (
        (AgentContactPlanInvalidDataError, 2),
        (AgentContactPlanProjectNotFoundError, 3),
        (AgentContactPlanCompanyNotFoundError, 4),
        (AgentContactPlanBindingMismatchError, 5),
        (AgentContactPlanWebsiteMissingError, 6),
        (AgentContactPlanProviderError, 7),
        (AgentContactPlanDiscoveryResultError, 8),
        (AgentContactPlanPersistenceError, 9),
        (AgentContactPlanSelectionConsistencyError, 10),
        (AgentContactPlanInternalError, 1),
    )


class _AgentContactPlanCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(arg in {"--help", "-h"} for arg in args):
            for option in _CONTACT_OPTIONS:
                occurrences = sum(arg == option or arg.startswith(f"{option}=") for arg in args)
                if occurrences > 1:
                    self._invalid_input()
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            self._invalid_input()

    @staticmethod
    def _invalid_input() -> Never:
        _load_contact_plan_dependencies()
        click.echo(_CONTACT_INVALID, err=True)
        raise click.exceptions.Exit(2)


def _contact_positive_integer(value: str) -> int:
    _load_contact_plan_dependencies()
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AgentContactPlanInvalidDataError(_CONTACT_INVALID) from None
    if parsed <= 0 or value.strip() != value:
        raise AgentContactPlanInvalidDataError(_CONTACT_INVALID)
    return parsed


def render_agent_contact_plan(result: AgentContactPlanResult, output: str) -> str:
    _load_contact_plan_dependencies()
    if type(result) is not AgentContactPlanResult or output not in {"text", "json"}:
        raise AgentContactPlanInternalError(_CONTACT_INTERNAL)
    try:
        snapshot = {field: getattr(result, field) for field in AgentContactPlanResult.model_fields}
        validated = AgentContactPlanResult(**snapshot)
        values = validated.model_dump(mode="json")
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise AgentContactPlanInternalError(_CONTACT_INTERNAL) from None
    if output == "json":
        rendered = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        rendered = "\n".join(
            f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
            for field in AgentContactPlanResult.model_fields
        )
    try:
        rendered.encode("utf-8")
    except UnicodeEncodeError:
        raise AgentContactPlanInternalError(_CONTACT_INTERNAL) from None
    return rendered


class _SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def execute_agent_contact_plan(
    data: AgentContactPlanInput,
    *,
    session_factory: _SessionFactory = SessionLocal,
    provider_factory: Callable[[], ContactDiscoveryProvider] | None = None,
) -> AgentContactPlanResult:
    _load_contact_plan_dependencies()
    if provider_factory is None:
        from app.modules.contact_discovery.website_provider import (
            WebsiteContactDiscoveryProvider,
        )

        provider_factory = WebsiteContactDiscoveryProvider
    return execute_contact_plan(
        data,
        session_factory=session_factory,
        provider_factory=provider_factory,
        components=ContactPlanComponents(
            project_repository=ProjectRepository,
            company_repository=CompanyRepository,
            discovery_repository=ContactDiscoveryRepository,
            plan_service=AgentContactPlanService,
        ),
    )


@contact_select_app.command("plan", cls=_AgentContactPlanCommand)
def plan_contact_selection(
    project_id: Annotated[str, typer.Option("--project-id", metavar="INTEGER", help="Project ID.")],
    company_id: Annotated[str, typer.Option("--company-id", metavar="INTEGER", help="Company ID.")],
    goal: Annotated[str, typer.Option("--goal", help="Planning goal.")],
    output: Annotated[str, typer.Option("--output", help="Output format: text or json.")] = "text",
) -> None:
    _load_contact_plan_dependencies()
    try:
        if output not in {"text", "json"}:
            raise AgentContactPlanInvalidDataError(_CONTACT_INVALID)
        data = AgentContactPlanInput(
            project_id=_contact_positive_integer(project_id),
            company_id=_contact_positive_integer(company_id),
            goal=goal,
        )
        rendered = render_agent_contact_plan(execute_agent_contact_plan(data), output)
    except ValidationError:
        typer.echo(_CONTACT_INVALID, err=True)
        raise typer.Exit(2) from None
    except AgentContactPlanError as error:
        exit_code = next(
            (
                code
                for error_type, code in _contact_plan_error_codes()
                if isinstance(error, error_type)
            ),
            1,
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(exit_code) from None
    except Exception:
        typer.echo(_CONTACT_INTERNAL, err=True)
        raise typer.Exit(1) from None
    typer.echo(rendered)


_INVALID_MESSAGE = "Agent company plan data is invalid."
_INTERNAL_MESSAGE = "Agent company plan failed."
_PLAN_OPTIONS = ("--project-id", "--search-profile-id", "--goal", "--output")


def _load_company_plan_dependencies() -> None:
    from app.modules.agent import company_plan, company_plan_schemas, company_selection

    namespace = globals()
    for name in (
        "AgentCompanyPlanBindingError",
        "AgentCompanyPlanDecisionError",
        "AgentCompanyPlanDiscoveryDataError",
        "AgentCompanyPlanError",
        "AgentCompanyPlanInternalError",
        "AgentCompanyPlanInvalidDataError",
        "AgentCompanyPlanPersistenceError",
        "AgentCompanyPlanProjectNotFoundError",
        "AgentCompanyPlanSearchProfileNotFoundError",
        "AgentCompanyPlanSearchProfileNotReadyError",
        "AgentCompanyPlanSearchProviderError",
        "AgentCompanyPlanSelectionError",
        "AgentCompanyPlanService",
    ):
        namespace.setdefault(name, getattr(company_plan, name))
    for name in ("AgentCompanyPlanInput", "AgentCompanyPlanResult"):
        namespace.setdefault(name, getattr(company_plan_schemas, name))
    for name in (
        "AgentCompanySelectionRepository",
        "AgentCompanySelectionService",
    ):
        namespace.setdefault(name, getattr(company_selection, name))


def SerpApiDiscoveryProvider(client: SerpApiClient) -> DiscoveryProvider:
    from app.modules.company_discovery.serpapi_provider import SerpApiDiscoveryProvider

    return SerpApiDiscoveryProvider(client)


class _OpenAIDecisionFactory:
    def __init__(self) -> None:
        self._client: OpenAIDecisionClient | None = None

    def __call__(self) -> DecisionBoundary:
        if self._client is not None:
            return self._client
        from app.core.config.settings import settings
        from app.providers.openai_decision import OpenAIDecisionClient

        self._client = OpenAIDecisionClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_output_tokens=settings.openai_max_output_tokens,
        )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def _company_plan_error_codes() -> tuple[tuple[type[AgentCompanyPlanError], int], ...]:
    _load_company_plan_dependencies()
    return (
        (AgentCompanyPlanInvalidDataError, 2),
        (AgentCompanyPlanProjectNotFoundError, 3),
        (AgentCompanyPlanSearchProfileNotFoundError, 3),
        (AgentCompanyPlanSearchProfileNotReadyError, 4),
        (AgentCompanyPlanSearchProviderError, 5),
        (AgentCompanyPlanDiscoveryDataError, 6),
        (AgentCompanyPlanPersistenceError, 7),
        (AgentCompanyPlanSelectionError, 8),
        (AgentCompanyPlanDecisionError, 9),
        (AgentCompanyPlanBindingError, 10),
        (AgentCompanyPlanInternalError, 1),
    )


class _AgentPlanCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(arg in {"--help", "-h"} for arg in args):
            for option in _PLAN_OPTIONS:
                occurrences = sum(arg == option or arg.startswith(f"{option}=") for arg in args)
                if occurrences > 1:
                    self._invalid_input()
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            self._invalid_input()

    @staticmethod
    def _invalid_input() -> Never:
        click.echo(_INVALID_MESSAGE, err=True)
        raise click.exceptions.Exit(2)


def execute_agent_company_plan(
    data: AgentCompanyPlanInput,
    *,
    session_factory: _SessionFactory = SessionLocal,
    decision_factory_factory: Callable[[], _OpenAIDecisionFactory] = (_OpenAIDecisionFactory),
    serpapi_client_factory: Callable[..., SerpApiClient] | None = None,
) -> AgentCompanyPlanResult:
    _load_company_plan_dependencies()
    if serpapi_client_factory is None:
        from app.providers.serpapi import SerpApiClient

        serpapi_client_factory = SerpApiClient
    return execute_company_plan(
        data,
        session_factory=session_factory,
        decision_factory_factory=decision_factory_factory,
        serpapi_client_factory=serpapi_client_factory,
        components=CompanyPlanComponents(
            staging_repository=CompanyDiscoveryStagingRepository,
            query_generator=SearchProfileQueryGenerator,
            project_repository=ProjectRepository,
            profile_repository=SearchProfileRepository,
            profile_service=SearchProfileService,
            staging_service=CompanyDiscoveryStagingService,
            selection_service=AgentCompanySelectionService,
            plan_service=AgentCompanyPlanService,
            discovery_provider=SerpApiDiscoveryProvider,
        ),
    )


def render_agent_company_plan(result: AgentCompanyPlanResult, output: str) -> str:
    _load_company_plan_dependencies()
    if type(result) is not AgentCompanyPlanResult or output not in {"text", "json"}:
        raise AgentCompanyPlanInternalError(_INTERNAL_MESSAGE)
    invalid = False
    validated: AgentCompanyPlanResult | None = None
    try:
        snapshot = {field: getattr(result, field) for field in AgentCompanyPlanResult.model_fields}
        validated = AgentCompanyPlanResult(**snapshot)
    except (AttributeError, TypeError, ValueError, ValidationError):
        invalid = True
    if invalid or validated is None:
        raise AgentCompanyPlanInternalError(_INTERNAL_MESSAGE)

    values = validated.model_dump(mode="json")
    if output == "json":
        rendered = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        rendered = "\n".join(
            f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
            for field in AgentCompanyPlanResult.model_fields
        )
    encoding_error = False
    try:
        rendered.encode("utf-8")
    except UnicodeEncodeError:
        encoding_error = True
    if encoding_error:
        raise AgentCompanyPlanInternalError(_INTERNAL_MESSAGE)
    return rendered


def _parse_positive_integer(value: str) -> int:
    _load_company_plan_dependencies()
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AgentCompanyPlanInvalidDataError(_INVALID_MESSAGE) from None
    if parsed <= 0 or value.strip() != value:
        raise AgentCompanyPlanInvalidDataError(_INVALID_MESSAGE)
    return parsed


@company_select_app.command("plan", cls=_AgentPlanCommand)
def plan_company_selection(
    project_id: Annotated[
        str,
        typer.Option("--project-id", metavar="INTEGER", help="Project ID."),
    ],
    search_profile_id: Annotated[
        str,
        typer.Option("--search-profile-id", metavar="INTEGER", help="Search profile ID."),
    ],
    goal: Annotated[str, typer.Option("--goal", help="Planning goal.")],
    output: Annotated[
        str,
        typer.Option("--output", help="Output format: text or json."),
    ] = "text",
) -> None:
    _load_company_plan_dependencies()
    try:
        if output not in {"text", "json"}:
            raise AgentCompanyPlanInvalidDataError(_INVALID_MESSAGE)
        data = AgentCompanyPlanInput(
            project_id=_parse_positive_integer(project_id),
            search_profile_id=_parse_positive_integer(search_profile_id),
            goal=goal,
        )
        result = execute_agent_company_plan(data)
        rendered = render_agent_company_plan(result, output)
    except ValidationError:
        typer.echo(_INVALID_MESSAGE, err=True)
        raise typer.Exit(2) from None
    except AgentCompanyPlanError as error:
        exit_code = next(
            (
                code
                for error_type, code in _company_plan_error_codes()
                if isinstance(error, error_type)
            ),
            1,
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(exit_code) from None
    except Exception:
        typer.echo(_INTERNAL_MESSAGE, err=True)
        raise typer.Exit(1) from None
    typer.echo(rendered)


_APPLY_INVALID = "Agent company apply data is invalid."
_APPLY_CONFIRMATION = "Agent company apply requires --yes."
_APPLY_INTERNAL = "Agent company apply failed."
_APPLY_OPTIONS = (
    "--project-id",
    "--discovery-run-id",
    "--candidate-id",
    "--yes",
    "--output",
)
_APPLY_FIELD_ORDER = tuple(AgentCompanyApplyResult.model_fields)
_APPLY_ERROR_CODES: tuple[tuple[type[AgentCompanyApplyError], int], ...] = (
    (AgentCompanyApplyInvalidDataError, 2),
    (AgentCompanyApplyConfirmationRequiredError, 3),
    (AgentCompanyApplyNotFoundError, 4),
    (AgentCompanyApplyStaleHandoffError, 5),
    (AgentCompanyApplyNotEligibleError, 6),
    (AgentCompanyApplyConsistencyError, 7),
    (AgentCompanyApplyConflictError, 8),
    (AgentCompanyApplyPersistenceError, 9),
    (AgentCompanyApplyInternalError, 1),
)


class _AgentApplyCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(arg in {"--help", "-h"} for arg in args):
            for option in _APPLY_OPTIONS:
                occurrences = sum(arg == option or arg.startswith(f"{option}=") for arg in args)
                if occurrences > 1:
                    self._invalid_input()
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            self._invalid_input()

    @staticmethod
    def _invalid_input() -> Never:
        click.echo(_APPLY_INVALID, err=True)
        raise click.exceptions.Exit(2)


def _apply_positive_integer(value: str) -> int:
    invalid = False
    parsed = 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        invalid = True
    if invalid or parsed <= 0 or value.strip() != value:
        raise AgentCompanyApplyInvalidDataError(_APPLY_INVALID)
    return parsed


def render_agent_company_apply(result: AgentCompanyApplyResult, output: str) -> str:
    if type(result) is not AgentCompanyApplyResult or output not in {"text", "json"}:
        raise AgentCompanyApplyInternalError(_APPLY_INTERNAL)
    invalid = False
    validated: AgentCompanyApplyResult | None = None
    try:
        snapshot = {field: getattr(result, field) for field in AgentCompanyApplyResult.model_fields}
        validated = AgentCompanyApplyResult(**snapshot)
    except (AttributeError, TypeError, ValueError, ValidationError):
        invalid = True
    if invalid or validated is None:
        raise AgentCompanyApplyInternalError(_APPLY_INTERNAL)
    values = validated.model_dump(mode="json")
    if output == "json":
        rendered = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        rendered = "\n".join(
            f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
            for field in _APPLY_FIELD_ORDER
        )
    encoding_failed = False
    try:
        rendered.encode("utf-8")
    except UnicodeEncodeError:
        encoding_failed = True
    if encoding_failed:
        raise AgentCompanyApplyInternalError(_APPLY_INTERNAL)
    return rendered


def _cleanup_preserving_primary(operation: Callable[[], object]) -> None:
    cleanup_failed = False
    try:
        operation()
    except BaseException:
        cleanup_failed = True
    if cleanup_failed:
        return


def _execute_agent_company_apply(
    data: AgentCompanyApplyInput,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
) -> str:
    rendered: str | None = None

    def prepare_result(result: AgentCompanyApplyResult) -> None:
        nonlocal rendered
        rendered = render_agent_company_apply(result, output)

    execute_company_apply(
        data,
        session_factory=session_factory,
        components=CompanyApplyComponents(
            staging_repository=CompanyDiscoveryStagingRepository,
            company_repository=CompanyRepository,
            review_service=CompanyDiscoveryCandidateReviewService,
            promotion_service=CompanyDiscoveryCandidatePromotionService,
            apply_service=AgentCompanyApplyService,
        ),
        before_commit=prepare_result,
    )
    if rendered is None:
        raise AgentCompanyApplyInternalError(_APPLY_INTERNAL)
    return rendered


@company_select_app.command("apply", cls=_AgentApplyCommand)
def apply_company_selection(
    project_id: Annotated[str, typer.Option("--project-id", metavar="INTEGER", help="Project ID.")],
    discovery_run_id: Annotated[
        str,
        typer.Option("--discovery-run-id", metavar="INTEGER", help="Discovery run ID."),
    ],
    candidate_id: Annotated[
        str, typer.Option("--candidate-id", metavar="INTEGER", help="Candidate ID.")
    ],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm Company apply.")] = False,
    output: Annotated[str, typer.Option("--output", help="Output format: text or json.")] = "text",
) -> None:
    if not yes:
        typer.echo(_APPLY_CONFIRMATION, err=True)
        raise typer.Exit(3)
    try:
        if output not in {"text", "json"}:
            raise AgentCompanyApplyInvalidDataError(_APPLY_INVALID)
        data = AgentCompanyApplyInput(
            project_id=_apply_positive_integer(project_id),
            discovery_run_id=_apply_positive_integer(discovery_run_id),
            candidate_id=_apply_positive_integer(candidate_id),
            confirmed=True,
        )
        rendered = _execute_agent_company_apply(data, output)
    except ValidationError:
        typer.echo(_APPLY_INVALID, err=True)
        raise typer.Exit(2) from None
    except AgentCompanyApplyError as error:
        exit_code = next(
            (code for error_type, code in _APPLY_ERROR_CODES if isinstance(error, error_type)), 1
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(exit_code) from None
    except Exception:
        typer.echo(_APPLY_INTERNAL, err=True)
        raise typer.Exit(1) from None
    typer.echo(rendered)


_CONTACT_APPLY_INVALID = "Agent contact apply data is invalid."
_CONTACT_APPLY_CONFIRMATION = "Agent contact apply requires --yes."
_CONTACT_APPLY_INTERNAL = "Agent contact apply failed."
_CONTACT_APPLY_OPTIONS = (
    "--project-id",
    "--company-id",
    "--candidate-id",
    "--goal",
    "--handoff-token",
    "--yes",
    "--output",
)
_CONTACT_APPLY_FIELD_ORDER = tuple(AgentContactApplyResult.model_fields)
_CONTACT_APPLY_ERROR_CODES: tuple[tuple[type[AgentContactApplyError], int], ...] = (
    (AgentContactApplyInvalidDataError, 2),
    (AgentContactApplyConfirmationRequiredError, 3),
    (AgentContactApplyNotFoundError, 4),
    (AgentContactApplyStaleHandoffError, 5),
    (AgentContactApplyNotEligibleError, 6),
    (AgentContactApplyConsistencyError, 7),
    (AgentContactApplyConflictError, 8),
    (AgentContactApplyPersistenceError, 9),
    (AgentContactApplyInternalError, 1),
)


class _AgentContactApplyCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(arg in {"--help", "-h"} for arg in args):
            if any(arg.startswith("--yes=") for arg in args):
                self._confirmation_required()
            for option in _CONTACT_APPLY_OPTIONS:
                occurrences = sum(arg == option or arg.startswith(f"{option}=") for arg in args)
                if occurrences > 1:
                    self._invalid_input()
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            self._invalid_input()

    @staticmethod
    def _invalid_input() -> Never:
        click.echo(_CONTACT_APPLY_INVALID, err=True)
        raise click.exceptions.Exit(2)

    @staticmethod
    def _confirmation_required() -> Never:
        click.echo(_CONTACT_APPLY_CONFIRMATION, err=True)
        raise click.exceptions.Exit(3)


def _contact_apply_positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AgentContactApplyInvalidDataError(_CONTACT_APPLY_INVALID) from None
    if parsed <= 0 or value.strip() != value:
        raise AgentContactApplyInvalidDataError(_CONTACT_APPLY_INVALID)
    return parsed


def render_agent_contact_apply(result: AgentContactApplyResult, output: str) -> str:
    if type(result) is not AgentContactApplyResult or output not in {"text", "json"}:
        raise AgentContactApplyInternalError(_CONTACT_APPLY_INTERNAL)
    try:
        snapshot = {field: getattr(result, field) for field in AgentContactApplyResult.model_fields}
        validated = AgentContactApplyResult(**snapshot)
        values = validated.model_dump(mode="json")
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise AgentContactApplyInternalError(_CONTACT_APPLY_INTERNAL) from None
    if output == "json":
        rendered = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        rendered = "\n".join(
            f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
            for field in _CONTACT_APPLY_FIELD_ORDER
        )
    try:
        rendered.encode("utf-8")
    except UnicodeEncodeError:
        raise AgentContactApplyInternalError(_CONTACT_APPLY_INTERNAL) from None
    return rendered


def _execute_agent_contact_apply(
    data: AgentContactApplyInput,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
) -> str:
    rendered: str | None = None

    def prepare_result(result: AgentContactApplyResult) -> None:
        nonlocal rendered
        rendered = render_agent_contact_apply(result, output)

    execute_contact_apply(
        data,
        session_factory=session_factory,
        components=ContactApplyComponents(
            company_repository=CompanyRepository,
            contact_repository=ContactRepository,
            discovery_repository=ContactDiscoveryRepository,
            review_service=ContactDiscoveryCandidateReviewService,
            promotion_service=ContactDiscoveryCandidatePromotionService,
            lead_repository=LeadRepository,
            task_repository=TaskRepository,
            apply_service=AgentContactApplyService,
        ),
        before_commit=prepare_result,
    )
    if rendered is None:
        raise AgentContactApplyInternalError(_CONTACT_APPLY_INTERNAL)
    return rendered


@contact_select_app.command("apply", cls=_AgentContactApplyCommand)
def apply_contact_selection(
    project_id: Annotated[str, typer.Option("--project-id", metavar="INTEGER")],
    company_id: Annotated[str, typer.Option("--company-id", metavar="INTEGER")],
    candidate_id: Annotated[str, typer.Option("--candidate-id", metavar="INTEGER")],
    goal: Annotated[str, typer.Option("--goal")],
    handoff_token: Annotated[str, typer.Option("--handoff-token")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm Contact apply.")] = False,
    output: Annotated[str, typer.Option("--output", help="Output format: text or json.")] = "text",
) -> None:
    if not yes:
        typer.echo(_CONTACT_APPLY_CONFIRMATION, err=True)
        raise typer.Exit(3)
    try:
        if output not in {"text", "json"}:
            raise AgentContactApplyInvalidDataError(_CONTACT_APPLY_INVALID)
        data = AgentContactApplyInput(
            project_id=_contact_apply_positive_integer(project_id),
            company_id=_contact_apply_positive_integer(company_id),
            candidate_id=_contact_apply_positive_integer(candidate_id),
            goal=goal,
            handoff_token=handoff_token,
            confirmed=True,
        )
        rendered = _execute_agent_contact_apply(data, output)
    except ValidationError:
        typer.echo(_CONTACT_APPLY_INVALID, err=True)
        raise typer.Exit(2) from None
    except AgentContactApplyError as error:
        exit_code = next(
            (
                code
                for error_type, code in _CONTACT_APPLY_ERROR_CODES
                if isinstance(error, error_type)
            ),
            1,
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(exit_code) from None
    except Exception:
        typer.echo(_CONTACT_APPLY_INTERNAL, err=True)
        raise typer.Exit(1) from None
    typer.echo(rendered)


__all__ = [
    "app",
    "execute_agent_lead_acquisition",
    "execute_agent_company_plan",
    "execute_agent_contact_plan",
    "render_agent_lead_acquisition",
    "render_agent_company_plan",
    "render_agent_contact_plan",
]

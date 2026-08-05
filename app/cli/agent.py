import json
from collections.abc import Callable
from contextlib import suppress
from typing import Annotated, Never, Protocol, cast

import typer
from pydantic import ValidationError
from sqlalchemy.orm import Session
from typer import _click as click
from typer._click.exceptions import UsageError
from typer.core import TyperCommand

from app.core.config.settings import settings
from app.core.database.session import SessionLocal
from app.modules.agent import (
    AgentCompanyPlanBindingError,
    AgentCompanyPlanDecisionError,
    AgentCompanyPlanDiscoveryDataError,
    AgentCompanyPlanError,
    AgentCompanyPlanInput,
    AgentCompanyPlanInternalError,
    AgentCompanyPlanInvalidDataError,
    AgentCompanyPlanPersistenceError,
    AgentCompanyPlanProjectNotFoundError,
    AgentCompanyPlanResult,
    AgentCompanyPlanSearchProfileNotFoundError,
    AgentCompanyPlanSearchProfileNotReadyError,
    AgentCompanyPlanSearchProviderError,
    AgentCompanyPlanSelectionError,
    AgentCompanyPlanService,
    AgentCompanySelectionService,
)
from app.modules.agent.company_plan import DecisionBoundary
from app.modules.agent.company_selection import AgentCompanySelectionRepository
from app.modules.company_discovery import SerpApiDiscoveryProvider
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.schemas import DiscoveryProviderResponse
from app.modules.company_discovery.staging_orchestration import (
    CompanyDiscoveryStagingService,
)
from app.modules.company_discovery.staging_repository import (
    CompanyDiscoveryStagingRepository,
)
from app.modules.project import ProjectRepository
from app.modules.search_profile import (
    SearchProfileQueryGenerator,
    SearchProfileRepository,
    SearchProfileService,
)
from app.modules.search_profile.schemas import SearchQuery
from app.providers.openai_decision import OpenAIDecisionClient
from app.providers.serpapi import SerpApiClient

app = typer.Typer(help="Bounded Agent planning commands.")
company_select_app = typer.Typer(help="Company-selection planning commands.")
app.add_typer(company_select_app, name="company-select")

_INVALID_MESSAGE = "Agent company plan data is invalid."
_INTERNAL_MESSAGE = "Agent company plan failed."
_FIELD_ORDER = tuple(AgentCompanyPlanResult.model_fields)
_PLAN_OPTIONS = ("--project-id", "--search-profile-id", "--goal", "--output")
_ERROR_CODES: tuple[tuple[type[AgentCompanyPlanError], int], ...] = (
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


class _SessionFactory(Protocol):
    def __call__(self) -> Session: ...


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


class _DiscoveryCommitter:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.committed = False

    def commit_discovery(self) -> None:
        self._session.commit()
        self.committed = True


class _CountedDiscoveryProvider:
    def __init__(self, provider: DiscoveryProvider) -> None:
        self._provider = provider
        self._call_count = 0
        self._last_query: str | None = None

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        self._call_count += 1
        self._last_query = query.text
        return self._provider.search(query)

    def snapshot_call_count(self) -> int:
        return self._call_count

    def last_query(self) -> str | None:
        return self._last_query


class _OpenAIDecisionFactory:
    def __init__(self) -> None:
        self._client: OpenAIDecisionClient | None = None

    def __call__(self) -> DecisionBoundary:
        if self._client is not None:
            return self._client
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


class _LazyDecisionFactory:
    def __init__(
        self,
        factory_factory: Callable[[], _OpenAIDecisionFactory],
    ) -> None:
        self._factory_factory = factory_factory
        self._factory: _OpenAIDecisionFactory | None = None

    def __call__(self) -> DecisionBoundary:
        boundary: DecisionBoundary | None = None
        error: AgentCompanyPlanDecisionError | None = None
        try:
            if self._factory is None:
                self._factory = self._factory_factory()
            boundary = self._factory()
        except Exception:
            error = AgentCompanyPlanDecisionError("Company decision provider failed.")
        if error is not None:
            raise error
        return cast(DecisionBoundary, boundary)

    def close(self) -> None:
        if self._factory is not None:
            self._factory.close()


def _provider_construction[T](operation: Callable[[], T]) -> T:
    error: AgentCompanyPlanSearchProviderError | None = None
    value: T | None = None
    try:
        value = operation()
    except Exception:
        error = AgentCompanyPlanSearchProviderError("Company search provider failed.")
    if error is not None:
        raise error
    return cast(T, value)


def execute_agent_company_plan(
    data: AgentCompanyPlanInput,
    *,
    session_factory: _SessionFactory = SessionLocal,
    decision_factory_factory: Callable[[], _OpenAIDecisionFactory] = (_OpenAIDecisionFactory),
    serpapi_client_factory: Callable[..., SerpApiClient] = SerpApiClient,
) -> AgentCompanyPlanResult:
    session = session_factory()
    committer: _DiscoveryCommitter | None = None
    decision_factory = _LazyDecisionFactory(decision_factory_factory)
    try:
        committer = _DiscoveryCommitter(session)
        staging_repository = CompanyDiscoveryStagingRepository(session)
        query_generator = SearchProfileQueryGenerator()
        serpapi_client = _provider_construction(
            lambda: serpapi_client_factory(
                api_key=settings.serpapi_api_key,
                base_url=settings.serpapi_base_url,
                timeout_seconds=settings.serpapi_timeout_seconds,
            )
        )
        concrete_provider = _provider_construction(lambda: SerpApiDiscoveryProvider(serpapi_client))
        counted_provider = _CountedDiscoveryProvider(concrete_provider)
        service = AgentCompanyPlanService(
            projects=ProjectRepository(session),
            profiles=SearchProfileService(SearchProfileRepository(session)),
            staging=CompanyDiscoveryStagingService(
                repository=staging_repository,
                query_generator=query_generator,
            ),
            staging_provider=counted_provider,
            staging_repository=staging_repository,
            provider_telemetry=counted_provider,
            committer=committer,
            selection=AgentCompanySelectionService(
                cast(AgentCompanySelectionRepository, staging_repository)
            ),
            decision_factory=decision_factory,
        )
        return service.plan(data)
    except BaseException:
        with suppress(Exception):
            session.rollback()
        raise
    finally:
        with suppress(Exception):
            decision_factory.close()
        session.close()


def render_agent_company_plan(result: AgentCompanyPlanResult, output: str) -> str:
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
            for field in _FIELD_ORDER
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
            (code for error_type, code in _ERROR_CODES if isinstance(error, error_type)),
            1,
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(exit_code) from None
    except Exception:
        typer.echo(_INTERNAL_MESSAGE, err=True)
        raise typer.Exit(1) from None
    typer.echo(rendered)


__all__ = ["app", "execute_agent_company_plan", "render_agent_company_plan"]

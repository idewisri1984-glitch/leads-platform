import json
from collections.abc import Callable
from contextlib import suppress
from typing import Annotated, Protocol, cast

import typer
from pydantic import ValidationError
from sqlalchemy.orm import Session

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
from app.modules.agent.company_selection import AgentCompanySelectionRepository
from app.modules.company_discovery import SerpApiDiscoveryProvider
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
from app.providers.openai_decision import (
    OpenAIDecisionClient,
    OpenAIDecisionRequest,
    OpenAIDecisionResult,
)
from app.providers.serpapi import SerpApiClient

app = typer.Typer(help="Bounded Agent planning commands.")
company_select_app = typer.Typer(help="Company-selection planning commands.")
app.add_typer(company_select_app, name="company-select")

_FIELD_ORDER = tuple(AgentCompanyPlanResult.model_fields)
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


class _DiscoveryCommitter:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.committed = False

    def commit_discovery(self) -> None:
        self._session.commit()
        self.committed = True


class _LazyOpenAIDecisionBoundary:
    def __init__(self) -> None:
        self._client: OpenAIDecisionClient | None = None

    def decide(self, request: OpenAIDecisionRequest) -> OpenAIDecisionResult:
        if self._client is None:
            self._client = OpenAIDecisionClient(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout_seconds=settings.openai_timeout_seconds,
                max_output_tokens=settings.openai_max_output_tokens,
            )
        return self._client.decide(request)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def execute_agent_company_plan(
    data: AgentCompanyPlanInput,
    *,
    session_factory: _SessionFactory = SessionLocal,
    decision_factory: Callable[[], _LazyOpenAIDecisionBoundary] = (_LazyOpenAIDecisionBoundary),
) -> AgentCompanyPlanResult:
    session = session_factory()
    committer = _DiscoveryCommitter(session)
    decision = decision_factory()
    try:
        staging_repository = CompanyDiscoveryStagingRepository(session)
        query_generator = SearchProfileQueryGenerator()
        serpapi_client = SerpApiClient(
            api_key=settings.serpapi_api_key,
            base_url=settings.serpapi_base_url,
            timeout_seconds=settings.serpapi_timeout_seconds,
        )
        service = AgentCompanyPlanService(
            projects=ProjectRepository(session),
            profiles=SearchProfileService(SearchProfileRepository(session)),
            query_generator=query_generator,
            staging=CompanyDiscoveryStagingService(
                repository=staging_repository,
                query_generator=query_generator,
            ),
            staging_provider=SerpApiDiscoveryProvider(serpapi_client),
            staging_repository=staging_repository,
            committer=committer,
            selection=AgentCompanySelectionService(
                cast(AgentCompanySelectionRepository, staging_repository)
            ),
            decision=decision,
        )
        return service.plan(data)
    except BaseException:
        if not committer.committed:
            with suppress(Exception):
                session.rollback()
        raise
    finally:
        with suppress(Exception):
            decision.close()
        session.close()


def render_agent_company_plan(result: AgentCompanyPlanResult, output: str) -> str:
    values = result.model_dump(mode="json")
    if output == "json":
        return json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return "\n".join(
        f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
        for field in _FIELD_ORDER
    )


def _parse_positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AgentCompanyPlanInvalidDataError("Agent company plan data is invalid.") from None
    if parsed <= 0 or value.strip() != value:
        raise AgentCompanyPlanInvalidDataError("Agent company plan data is invalid.")
    return parsed


@company_select_app.command("plan")
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
            raise AgentCompanyPlanInvalidDataError("Agent company plan data is invalid.")
        data = AgentCompanyPlanInput(
            project_id=_parse_positive_integer(project_id),
            search_profile_id=_parse_positive_integer(search_profile_id),
            goal=goal,
        )
        result = execute_agent_company_plan(data)
    except ValidationError:
        typer.echo("Agent company plan data is invalid.", err=True)
        raise typer.Exit(2) from None
    except AgentCompanyPlanError as error:
        exit_code = next(
            (code for error_type, code in _ERROR_CODES if isinstance(error, error_type)),
            1,
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(exit_code) from None
    except Exception:
        typer.echo("Agent company plan failed.", err=True)
        raise typer.Exit(1) from None
    typer.echo(render_agent_company_plan(result, output))


__all__ = ["app", "execute_agent_company_plan", "render_agent_company_plan"]

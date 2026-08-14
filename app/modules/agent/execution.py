from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.exc import IntegrityError

from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.schemas import DiscoveryProviderResponse
from app.modules.search_profile.schemas import SearchQuery

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.modules.agent.company_apply_schemas import (
        AgentCompanyApplyInput,
        AgentCompanyApplyResult,
    )
    from app.modules.agent.company_plan import DecisionBoundary
    from app.modules.agent.company_plan_schemas import (
        AgentCompanyPlanInput,
        AgentCompanyPlanResult,
    )
    from app.modules.agent.contact_apply_schemas import (
        AgentContactApplyInput,
        AgentContactApplyResult,
    )
    from app.modules.agent.contact_plan_schemas import (
        AgentContactPlanInput,
        AgentContactPlanResult,
    )


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


@dataclass(frozen=True)
class CompanyPlanComponents:
    staging_repository: Callable[[Session], Any]
    query_generator: Callable[[], Any]
    project_repository: Callable[[Session], Any]
    profile_repository: Callable[[Session], Any]
    profile_service: Callable[[Any], Any]
    staging_service: Callable[..., Any]
    selection_service: Callable[[Any], Any]
    plan_service: Callable[..., Any]
    discovery_provider: Callable[[Any], DiscoveryProvider]


@dataclass(frozen=True)
class CompanyApplyComponents:
    staging_repository: Callable[[Session], Any]
    company_repository: Callable[[Session], Any]
    review_service: Callable[[Any], Any]
    promotion_service: Callable[[Any, Any], Any]
    apply_service: Callable[..., Any]


@dataclass(frozen=True)
class ContactPlanComponents:
    project_repository: Callable[[Session], Any]
    company_repository: Callable[[Session], Any]
    discovery_repository: Callable[[Session], Any]
    plan_service: Callable[..., Any]


@dataclass(frozen=True)
class ContactApplyComponents:
    company_repository: Callable[[Session], Any]
    contact_repository: Callable[[Session], Any]
    discovery_repository: Callable[[Session], Any]
    review_service: Callable[[Any], Any]
    promotion_service: Callable[[Any, Any], Any]
    lead_repository: Callable[[Session], Any]
    task_repository: Callable[[Session], Any]
    apply_service: Callable[..., Any]


class _DiscoveryCommitter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit_discovery(self) -> None:
        self._session.commit()


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


class _LazyDecisionFactory:
    def __init__(self, factory_factory: Callable[[], Any]) -> None:
        self._factory_factory = factory_factory
        self._factory: Any | None = None

    def __call__(self) -> DecisionBoundary:
        from app.modules.agent.company_plan import AgentCompanyPlanDecisionError

        boundary: DecisionBoundary | None = None
        failed = False
        try:
            if self._factory is None:
                self._factory = self._factory_factory()
            boundary = self._factory()
        except Exception:
            failed = True
        if failed:
            raise AgentCompanyPlanDecisionError("Company decision provider failed.")
        if boundary is None:
            raise AgentCompanyPlanDecisionError("Company decision provider failed.")
        return boundary

    def close(self) -> None:
        if self._factory is not None:
            self._factory.close()


def _provider_construction[T](operation: Callable[[], T]) -> T:
    from app.modules.agent.company_plan import AgentCompanyPlanSearchProviderError

    try:
        return operation()
    except Exception:
        raise AgentCompanyPlanSearchProviderError("Company search provider failed.") from None


def execute_company_plan(
    data: AgentCompanyPlanInput,
    *,
    session_factory: SessionFactory,
    components: CompanyPlanComponents,
    decision_factory_factory: Callable[[], Any],
    serpapi_client_factory: Callable[..., Any],
) -> AgentCompanyPlanResult:
    from app.core.config.settings import settings

    session = session_factory()
    decision_factory = _LazyDecisionFactory(decision_factory_factory)
    try:
        staging_repository = components.staging_repository(session)
        counted_provider = _CountedDiscoveryProvider(
            _provider_construction(
                lambda: components.discovery_provider(
                    serpapi_client_factory(
                        api_key=settings.serpapi_api_key,
                        base_url=settings.serpapi_base_url,
                        timeout_seconds=settings.serpapi_timeout_seconds,
                    )
                )
            )
        )
        service = components.plan_service(
            projects=components.project_repository(session),
            profiles=components.profile_service(components.profile_repository(session)),
            staging=components.staging_service(
                repository=staging_repository,
                query_generator=components.query_generator(),
            ),
            staging_provider=counted_provider,
            staging_repository=staging_repository,
            provider_telemetry=counted_provider,
            committer=_DiscoveryCommitter(session),
            selection=components.selection_service(staging_repository),
            decision_factory=decision_factory,
        )
        return cast("AgentCompanyPlanResult", service.plan(data))
    except BaseException:
        with suppress(Exception):
            session.rollback()
        raise
    finally:
        with suppress(Exception):
            decision_factory.close()
        session.close()


def _cleanup(operation: Callable[[], object]) -> None:
    with suppress(BaseException):
        operation()


def execute_company_apply(
    data: AgentCompanyApplyInput,
    *,
    session_factory: SessionFactory,
    components: CompanyApplyComponents,
    before_commit: Callable[[AgentCompanyApplyResult], object] | None = None,
) -> AgentCompanyApplyResult:
    from app.modules.agent.company_apply import (
        AgentCompanyApplyConflictError,
        AgentCompanyApplyPersistenceError,
    )

    session = session_factory()
    committed = False
    failed = False
    try:
        staging = components.staging_repository(session)
        companies = components.company_repository(session)
        service = components.apply_service(
            staging_repository=staging,
            company_repository=companies,
            review_service=components.review_service(staging),
            promotion_service=components.promotion_service(staging, companies),
        )
        result = cast("AgentCompanyApplyResult", service.apply(data))
        if before_commit is not None:
            before_commit(result)
        try:
            session.commit()
            committed = True
        except IntegrityError:
            raise AgentCompanyApplyConflictError(
                "Agent company apply persistence conflict."
            ) from None
        except Exception:
            raise AgentCompanyApplyPersistenceError(
                "Agent company apply could not be persisted."
            ) from None
        return result
    except BaseException:
        failed = True
        if not committed:
            _cleanup(session.rollback)
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


def execute_contact_plan(
    data: AgentContactPlanInput,
    *,
    session_factory: SessionFactory,
    components: ContactPlanComponents,
    provider_factory: Callable[[], Any],
) -> AgentContactPlanResult:
    from app.modules.agent.contact_plan import (
        AgentContactPlanInternalError,
        AgentContactPlanPersistenceError,
    )

    session = session_factory()
    committed = False
    failed = False
    try:
        repository = components.discovery_repository(session)
        result = cast(
            "AgentContactPlanResult",
            components.plan_service(
                projects=components.project_repository(session),
                companies=components.company_repository(session),
                discovery_repository=repository,
                provider_factory=provider_factory,
            ).plan(data),
        )
        try:
            session.commit()
            committed = True
        except Exception:
            raise AgentContactPlanPersistenceError(
                "Contact discovery state could not be persisted."
            ) from None
        return result
    except BaseException:
        failed = True
        if not committed:
            _cleanup(session.rollback)
        raise
    finally:
        if failed:
            _cleanup(session.close)
        else:
            try:
                session.close()
            except Exception:
                raise AgentContactPlanInternalError("Agent contact plan failed.") from None


def execute_contact_apply(
    data: AgentContactApplyInput,
    *,
    session_factory: SessionFactory,
    components: ContactApplyComponents,
    before_commit: Callable[[AgentContactApplyResult], object] | None = None,
) -> AgentContactApplyResult:
    from app.modules.agent.contact_apply import AgentContactApplyPersistenceError

    session = session_factory()
    committed = False
    failed = False
    try:
        discovery = components.discovery_repository(session)
        contacts = components.contact_repository(session)
        result = cast(
            "AgentContactApplyResult",
            components.apply_service(
                company_repository=components.company_repository(session),
                contact_repository=contacts,
                discovery_repository=discovery,
                review_service=components.review_service(discovery),
                promotion_service=components.promotion_service(discovery, contacts),
                lead_repository=components.lead_repository(session),
                task_repository=components.task_repository(session),
            ).apply(data),
        )
        if before_commit is not None:
            before_commit(result)
        try:
            session.commit()
            committed = True
        except Exception:
            raise AgentContactApplyPersistenceError(
                "Agent contact apply could not be persisted."
            ) from None
        return result
    except BaseException:
        failed = True
        if not committed:
            _cleanup(session.rollback)
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


__all__ = [
    "CompanyApplyComponents",
    "CompanyPlanComponents",
    "ContactApplyComponents",
    "ContactPlanComponents",
    "_LazyDecisionFactory",
    "execute_company_apply",
    "execute_company_plan",
    "execute_contact_apply",
    "execute_contact_plan",
]

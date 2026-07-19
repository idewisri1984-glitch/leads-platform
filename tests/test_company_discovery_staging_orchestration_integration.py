from collections.abc import Generator, Sequence
from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.profile_execution import SearchProfileDiscoveryService
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.schemas import (
    DiscoveryProviderResponse,
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryProviderError,
    SearchProfileDiscoveryQueryResult,
)
from app.modules.company_discovery.staging_orchestration import CompanyDiscoveryStagingService
from app.modules.company_discovery.staging_repository import (
    CompanyDiscoveryStagingRepository,
)
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
)
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingRunResult,
)
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.contact.models import Contact
from app.modules.project.models import Project
from app.modules.search_profile.models import SearchProfile
from app.modules.search_profile.query_generation import SearchProfileQueryGenerator
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)


class FakeProvider:
    provider_name = "serpapi"

    def search(self, query: SearchQuery) -> DiscoveryProviderResponse:
        raise AssertionError(f"provider search should not be called: {query.text}")


@dataclass
class RepositorySpyRun:
    id: int
    request_snapshot: CompanyDiscoveryRequestSnapshot
    request_fingerprint: str | None = None


@dataclass
class RepositorySpyUpsertResult:
    created: bool = False
    updated: bool = False
    protected: bool = False


class FakeQueryGenerator:
    def __init__(self, preview: SearchQueryPreview) -> None:
        self.preview = preview
        self.calls: list[tuple[SearchProfileRead, SearchProfileRunOptions | None]] = []

    def generate_preview(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchQueryPreview:
        self.calls.append((profile, options))
        return self.preview


class FakeExecutionService:
    def __init__(self, result: SearchProfileDiscoveryDryRunResult | BaseException) -> None:
        self.result = result
        self.calls: list[
            tuple[SearchProfileRead, DiscoveryProvider, SearchProfileRunOptions | None]
        ] = []

    def run_dry(
        self,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchProfileDiscoveryDryRunResult:
        self.calls.append((profile, provider, options))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class RepositorySpy:
    def __init__(
        self,
        *,
        fail_create: BaseException | None = None,
        fail_upsert_on_call: int | None = None,
        fail_upsert: BaseException | None = None,
        fail_update: BaseException | None = None,
        upsert_results: Sequence[RepositorySpyUpsertResult] | None = None,
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.create_run_args: list[CompanyDiscoveryRunCreate] = []
        self.upsert_candidate_args: list[tuple[int, int, CompanyDiscoveryCandidateCreate]] = []
        self.update_run_args: list[tuple[int, CompanyDiscoveryRunUpdate]] = []
        self._upsert_call = 0
        self._run_id = 1
        self.fail_create = fail_create
        self.fail_upsert_on_call = fail_upsert_on_call
        self.fail_upsert = fail_upsert
        self.fail_update = fail_update
        self._upsert_results = list(upsert_results or [RepositorySpyUpsertResult(created=True)])

    def create_run(self, data: CompanyDiscoveryRunCreate) -> RepositorySpyRun:
        self.calls.append(("create_run", data))
        self.create_run_args.append(data)
        if self.fail_create is not None:
            raise self.fail_create
        run = RepositorySpyRun(
            self._run_id,
            data.request_snapshot,
            data.request_snapshot.fingerprint(),
        )
        self._run_id += 1
        return run

    def upsert_candidate(
        self, *, project_id: int, run_id: int, data: CompanyDiscoveryCandidateCreate
    ) -> RepositorySpyUpsertResult:
        self.calls.append(("upsert_candidate", project_id, run_id, data))
        self.upsert_candidate_args.append((project_id, run_id, data))
        if self.fail_upsert_on_call is not None and self._upsert_call == self.fail_upsert_on_call:
            if self.fail_upsert is None:
                raise RuntimeError("upsert failed")
            raise self.fail_upsert
        self._upsert_call += 1
        if self._upsert_results:
            return self._upsert_results.pop(0)
        return RepositorySpyUpsertResult(created=True)

    def update_run(self, run_id: int, data: CompanyDiscoveryRunUpdate) -> None:
        self.calls.append(("update_run", run_id, data))
        self.update_run_args.append((run_id, data))
        if self.fail_update is not None:
            raise self.fail_update


@pytest.fixture
def sqlite_session() -> Generator[Session]:
    with SessionLocal() as session:
        yield session


def make_profile_read() -> SearchProfileRead:
    return SearchProfileRead(
        id=7,
        project_id=9,
        name="Buyer profile",
        description=None,
        product_or_service="Accounting software",
        target_customer_types=["firms"],
        target_industries=[],
        positive_keywords=[],
        negative_keywords=[],
        countries=["Germany"],
        cities=[],
        languages=[],
        query_templates=["{target_customer_type} {country}"],
        result_limit=10,
        max_queries_per_run=3,
        total_result_ceiling=25,
        enabled=True,
    )


def make_query(
    profile: SearchProfileRead, index: int, *, country_code: str | None = "DE"
) -> SearchQuery:
    return SearchQuery(
        text=f"search query {index}",
        profile_id=profile.id,
        profile_name=profile.name,
        language=None,
        country="Germany",
        city=None,
        source_template="{target_customer_type} {country}",
        country_code=country_code,
        limit=10,
    )


def make_query_result(
    query: SearchQuery,
    *,
    items: Sequence[CompanyIngestionItem] = (),
    provider_error: SearchProfileDiscoveryProviderError | None = None,
) -> SearchProfileDiscoveryQueryResult:
    return SearchProfileDiscoveryQueryResult(
        query=query,
        provider="serpapi",
        provider_result_count=len(items),
        adapted_item_count=len(items),
        adapter_error_count=0,
        provider_error=provider_error,
        items=list(items),
        adapter_errors=[],
    )


def make_item(
    *, name: str, row: int, website: str, country: str = "Germany"
) -> CompanyIngestionItem:
    return CompanyIngestionItem(
        source_row_number=row,
        name=name,
        website=website,
        country=country,
        city="Berlin",
    )


def make_dry_result(
    profile: SearchProfileRead,
    query_results: Sequence[SearchProfileDiscoveryQueryResult],
    *,
    query_count: int | None = None,
    estimated_provider_requests: int | None = None,
    stopped_early: bool = False,
    stop_reason: str | None = None,
) -> SearchProfileDiscoveryDryRunResult:
    actual_query_count = len(query_results) if query_count is None else query_count
    return SearchProfileDiscoveryDryRunResult(
        profile_id=profile.id,
        profile_name=profile.name,
        provider="serpapi",
        query_count=actual_query_count,
        estimated_provider_requests=(
            estimated_provider_requests
            if estimated_provider_requests is not None
            else actual_query_count
        ),
        executed_queries=len(query_results),
        total_provider_results=sum(result.provider_result_count for result in query_results),
        total_adapted_items=sum(result.adapted_item_count for result in query_results),
        total_adapter_errors=sum(result.adapter_error_count for result in query_results),
        total_provider_errors=sum(result.provider_error is not None for result in query_results),
        total_result_ceiling=25,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
        query_results=list(query_results),
    )


def make_profile_from_db(session: Session, project: Project) -> SearchProfile:
    profile = SearchProfile(
        project_id=project.id,
        name="Buyer profile",
        product_or_service="Accounting software",
        target_customer_types=["firms"],
        query_templates=["{target_customer_type} {country}"],
    )
    session.add(profile)
    session.flush()
    return profile


def run_orchestrator(
    *,
    profile: SearchProfileRead,
    provider: DiscoveryProvider,
    dry_run: bool,
    preview: SearchQueryPreview,
    execution_result: SearchProfileDiscoveryDryRunResult | BaseException,
    repository: object | None = None,
    options: SearchProfileRunOptions | None = None,
) -> CompanyDiscoveryStagingRunResult:
    repository_for_service = cast(CompanyDiscoveryStagingRepository | None, repository)
    service = CompanyDiscoveryStagingService(
        repository=repository_for_service,
        execution_service=cast(
            SearchProfileDiscoveryService, FakeExecutionService(execution_result)
        ),
        query_generator=cast(SearchProfileQueryGenerator, FakeQueryGenerator(preview)),
    )
    run_options = options or SearchProfileRunOptions()
    return service.run(
        profile=profile,
        provider=provider,
        options=run_options,
        dry_run=dry_run,
        repository=repository_for_service,
    )


def test_dry_run_success_no_mutation() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    dry_result = make_dry_result(
        profile,
        [
            make_query_result(
                query, items=[make_item(name="Acme", row=1, website="https://example.com")]
            )
        ],
    )
    repository = RepositorySpy()

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
        repository=repository,
    )
    assert repository.calls == []
    assert result.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert result.dry_run is True
    assert result.run_id is None
    assert result.run_persisted is False
    assert result.candidate_upserts == 0
    assert result.candidates_created == 0
    assert result.candidates_updated == 0
    assert result.candidates_protected == 0
    assert result.unique_candidate_count == 1
    assert len(result.candidates) == 1


def test_dry_run_not_found() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    dry_result = make_dry_result(profile, [make_query_result(query, items=())])
    repository = RepositorySpy()

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
        repository=repository,
    )

    assert result.status == CompanyDiscoveryRunStatus.NOT_FOUND
    assert result.error_code is None
    assert result.run_id is None
    assert repository.calls == []


def test_dry_run_partial_with_provider_error_and_safe_candidates() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    query_fail = make_query_result(
        query,
        provider_error=SearchProfileDiscoveryProviderError(
            code="request_error",
            message="temporary failure",
        ),
    )
    query_ok = make_query_result(
        query,
        items=[make_item(name="Acme", row=2, website="https://www.example.com/path")],
    )
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query, query],
    )
    dry_result = make_dry_result(
        profile,
        [query_ok, query_fail],
    )
    repository = RepositorySpy()

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
        repository=repository,
    )
    assert repository.calls == []
    assert result.status == CompanyDiscoveryRunStatus.PARTIAL
    assert result.error_code == "request_error"
    assert result.candidate_upserts == 0
    assert result.unique_candidate_count == 1
    assert len(result.candidates) == 1


def test_dry_run_failed_when_no_successful_queries() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    query_result = make_query_result(
        query,
        provider_error=SearchProfileDiscoveryProviderError(
            code="response_error",
            message="provider failure",
        ),
    )
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    dry_result = make_dry_result(profile, [query_result])
    repository = RepositorySpy()

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
        repository=repository,
    )

    assert result.status == CompanyDiscoveryRunStatus.FAILED
    assert result.error_code == "response_error"
    assert result.candidate_upserts == 0
    assert repository.calls == []


def test_dry_run_invalid_candidate_rejection_is_partial_and_counted() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    valid = make_query_result(
        query, items=[make_item(name="Acme", row=3, website="https://example.com/one")]
    )
    rejected = make_query_result(
        query, items=[make_item(name="Invalid", row=4, website="not-a-url")]
    )
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query, query],
    )
    dry_result = make_dry_result(profile, [valid, rejected], query_count=2)
    repository = RepositorySpy()

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
        repository=repository,
    )
    assert repository.calls == []
    assert result.status == CompanyDiscoveryRunStatus.PARTIAL
    assert result.error_code == "candidate_invalid"
    assert result.candidate_upserts == 0
    assert result.unique_candidate_count == 1
    assert len(result.candidates) == 1
    assert result.rejected_candidate_count == 1


def test_dry_run_duplicate_candidates_are_merged() -> None:
    profile = make_profile_read()
    query_a = make_query(profile, 1, country_code="DE")
    query_b = make_query(profile, 2, country_code="DE")
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query_a, query_b],
    )
    dry_result = make_dry_result(
        profile,
        [
            make_query_result(
                query_a,
                items=[make_item(name="Acme", row=5, website="https://www.example.com/path")],
            ),
            make_query_result(
                query_b,
                items=[make_item(name="Acme", row=2, website="https://example.com/other")],
            ),
        ],
    )
    repository = RepositorySpy()

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
        repository=repository,
    )
    assert repository.calls == []
    assert result.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert result.error_code is None
    assert result.candidate_upserts == 0
    assert result.unique_candidate_count == 1
    assert len(result.candidates) == 1


def test_dry_run_early_stop_preserves_reason_and_is_partial(sqlite_session: Session) -> None:
    _ = sqlite_session
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    dry_result = make_dry_result(
        profile,
        [
            make_query_result(
                query, items=[make_item(name="Acme", row=2, website="https://example.com")]
            )
        ],
        stopped_early=True,
        stop_reason="quota_exceeded",
    )

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=dry_result,
    )

    assert result.status == CompanyDiscoveryRunStatus.PARTIAL
    assert result.error_code == "quota_exceeded"
    assert result.candidate_upserts == 0
    assert result.unique_candidate_count == 1
    assert len(result.candidates) == 1
    assert result.stopped_early is True
    assert result.stop_reason == "quota_exceeded"


@pytest.mark.parametrize(
    "name",
    [
        "profile_id",
        "profile_name",
        "provider",
        "query_count",
    ],
)
def test_dry_run_invalid_execution_result_returns_execution_invalid(name: str) -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )

    if name == "profile_id":
        bad_result = make_dry_result(profile, [make_query_result(query, items=())], query_count=1)
        bad_result = bad_result.model_copy(update={"profile_id": profile.id + 1})
    elif name == "profile_name":
        bad_result = make_dry_result(profile, [make_query_result(query, items=())]).model_copy(
            update={"profile_name": "Wrong"}
        )
    elif name == "provider":
        bad_result = make_dry_result(profile, [make_query_result(query, items=())]).model_copy(
            update={"provider": "other"}
        )
    else:
        bad_result = make_dry_result(profile, [make_query_result(query, items=())], query_count=2)

    repository = RepositorySpy()
    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=bad_result,
        repository=repository,
    )

    assert result.status == CompanyDiscoveryRunStatus.FAILED
    assert result.error_code == "execution_invalid"
    assert result.run_id is None
    assert result.candidate_upserts == 0
    assert all(call[0] != "upsert_candidate" for call in repository.calls)


def test_dry_run_no_state_change_on_exception(sqlite_session: Session) -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    project_count = sqlite_session.scalar(select(func.count()).select_from(CompanyDiscoveryRun))
    candidate_count = sqlite_session.scalar(
        select(func.count()).select_from(CompanyDiscoveryCandidate)
    )
    assert project_count is not None and candidate_count is not None

    result = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=True,
        preview=preview,
        execution_result=RuntimeError("boom"),
    )

    assert result.status == CompanyDiscoveryRunStatus.FAILED
    assert result.run_persisted is False
    assert (
        sqlite_session.scalar(select(func.count()).select_from(CompanyDiscoveryRun))
        == project_count
    )
    assert (
        sqlite_session.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate))
        == candidate_count
    )


def test_snapshot_contract_with_real_persisted_run(sqlite_session: Session) -> None:
    project = Project(name="Snapshot persisted project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1)
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=7,
        total_result_ceiling=11,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)
    report = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(profile_read, [make_query_result(query, items=())]),
        repository=repository,
    )

    run = sqlite_session.get(CompanyDiscoveryRun, report.run_id)
    assert run is not None
    assert run.request_snapshot["source_mode"] == "SEARCH_PROFILE"
    assert run.request_snapshot["search_profile_id"] == profile_read.id
    assert run.request_snapshot["country_codes"] == []
    assert run.request_snapshot["query_count"] == 1
    assert run.request_snapshot["result_limit"] == 7
    assert run.request_snapshot["total_result_ceiling"] == 11
    for key in [
        "query",
        "query_text",
        "countries",
        "city",
        "snippet",
        "notes",
        "provider_reference",
        "request_url",
        "headers",
    ]:
        assert key not in run.request_snapshot

    options_report = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(profile_read, [make_query_result(query, items=())]),
        repository=repository,
        options=SearchProfileRunOptions(country_codes=("de", "US")),
    )
    options_run = sqlite_session.get(CompanyDiscoveryRun, options_report.run_id)
    assert options_run is not None
    assert options_run.request_snapshot["country_codes"] == ["DE", "US"]


def test_persisted_successful_run_and_counts(sqlite_session: Session) -> None:
    project = Project(name="Success project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1)
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)
    run = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile_read,
            [
                make_query_result(
                    query, items=[make_item(name="Acme", row=1, website="https://example.com/path")]
                )
            ],
        ),
        repository=repository,
    )
    sqlite_session.commit()

    assert run.run_id is not None
    assert run.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert run.candidate_upserts == 1
    assert run.run_persisted is True
    db_run_id = run.run_id
    candidate_count = sqlite_session.scalar(
        select(func.count())
        .select_from(CompanyDiscoveryCandidate)
        .where(CompanyDiscoveryCandidate.last_seen_run_id == db_run_id)
    )
    assert candidate_count == 1


def test_persisted_status_matrix(sqlite_session: Session) -> None:
    project = Project(name="Matrix project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1)
    query_b = make_query(profile_read, 2, country_code="DE")
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query, query_b],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)

    cases = [
        (
            CompanyDiscoveryRunStatus.SUCCEEDED,
            make_dry_result(
                profile_read,
                [
                    make_query_result(
                        query,
                        items=[make_item(name="Acme", row=1, website="https://example.com/a")],
                    ),
                    make_query_result(
                        query_b,
                        items=[make_item(name="Acme", row=2, website="https://example.com/b")],
                    ),
                ],
            ),
            1,
        ),
        (
            CompanyDiscoveryRunStatus.NOT_FOUND,
            make_dry_result(
                profile_read,
                [make_query_result(query, items=()), make_query_result(query_b, items=())],
            ),
            0,
        ),
        (
            CompanyDiscoveryRunStatus.PARTIAL,
            make_dry_result(
                profile_read,
                [
                    make_query_result(
                        query,
                        provider_error=SearchProfileDiscoveryProviderError(
                            code="request_error",
                            message="temporary failure",
                        ),
                    ),
                    make_query_result(
                        query_b,
                        items=[make_item(name="Acme", row=3, website="https://example.com/c")],
                    ),
                ],
            ),
            1,
        ),
        (
            CompanyDiscoveryRunStatus.PARTIAL,
            make_dry_result(
                profile_read,
                [
                    make_query_result(
                        query,
                        provider_error=SearchProfileDiscoveryProviderError(
                            code="response_error",
                            message="bad response",
                        ),
                    ),
                    make_query_result(query_b),
                ],
            ),
            0,
        ),
    ]

    for expected_status, dry_result, expected_candidates in cases:
        result = run_orchestrator(
            profile=profile_read,
            provider=FakeProvider(),
            dry_run=False,
            preview=preview,
            execution_result=dry_result,
            repository=repository,
        )
        sqlite_session.commit()
        assert result.status == expected_status
        assert sqlite_session.get(CompanyDiscoveryRun, result.run_id) is not None
        assert result.candidate_upserts == expected_candidates


def test_persisted_rollback_disappears_partial_writes(sqlite_session: Session) -> None:
    project = Project(name="Rollback project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1)
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)
    service = CompanyDiscoveryStagingService(
        repository=repository,
        execution_service=cast(
            SearchProfileDiscoveryService,
            FakeExecutionService(
                make_dry_result(
                    profile_read,
                    [
                        make_query_result(
                            query,
                            items=[make_item(name="Acme", row=1, website="https://example.com/a")],
                        )
                    ],
                )
            ),
        ),
        query_generator=cast(SearchProfileQueryGenerator, FakeQueryGenerator(preview)),
    )

    report = service.run(
        profile=profile_read,
        provider=FakeProvider(),
        options=SearchProfileRunOptions(),
        dry_run=False,
        repository=repository,
    )
    assert report.run_id is not None
    run_id = report.run_id
    sqlite_session.rollback()

    with SessionLocal() as verify:
        assert verify.get(CompanyDiscoveryRun, run_id) is None
        assert verify.scalar(select(func.count()).select_from(CompanyDiscoveryCandidate)) == 0


def test_repeated_runs_preserve_candidate_identity_and_updates(sqlite_session: Session) -> None:
    project = Project(name="Repeat project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1, country_code="DE")
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)

    first = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile_read,
            [
                make_query_result(
                    query,
                    items=[make_item(name="Acme", row=4, website="https://www.example.com/path")],
                )
            ],
        ),
        repository=repository,
    )
    sqlite_session.commit()

    second = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile_read,
            [
                make_query_result(
                    query, items=[make_item(name="Acme", row=2, website="https://example.com/path")]
                )
            ],
        ),
        repository=repository,
    )
    sqlite_session.commit()

    assert first.run_persisted is True
    assert second.run_persisted is True
    assert first.run_id is not None and second.run_id is not None
    assert second.run_id > first.run_id

    run_count = sqlite_session.scalar(select(func.count()).select_from(CompanyDiscoveryRun))
    candidate_count = sqlite_session.scalar(
        select(func.count()).select_from(CompanyDiscoveryCandidate)
    )
    candidate = sqlite_session.scalar(select(CompanyDiscoveryCandidate))
    assert run_count == 2
    assert candidate_count == 1
    assert candidate is not None
    assert candidate.first_seen_run_id == first.run_id
    assert candidate.last_seen_run_id == second.run_id
    assert candidate.best_position == 2


@pytest.mark.parametrize(
    "status",
    [
        CompanyDiscoveryCandidateStatus.REVIEWED,
        CompanyDiscoveryCandidateStatus.PROMOTED,
        CompanyDiscoveryCandidateStatus.REJECTED,
    ],
)
def test_protected_candidate_status_is_not_reset(
    sqlite_session: Session, status: CompanyDiscoveryCandidateStatus
) -> None:
    project = Project(name="Protected project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1)
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)

    first = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile_read,
            [
                make_query_result(
                    query,
                    items=[make_item(name="Acme", row=1, website="https://www.example.com/path")],
                )
            ],
        ),
        repository=repository,
    )
    sqlite_session.flush()

    candidate = sqlite_session.scalar(
        select(CompanyDiscoveryCandidate).where(CompanyDiscoveryCandidate.project_id == project.id)
    )
    assert candidate is not None
    candidate.candidate_status = status
    if status == CompanyDiscoveryCandidateStatus.PROMOTED:
        company = Company(project_id=project.id, name="Promoted")
        sqlite_session.add(company)
        sqlite_session.flush()
        candidate.promoted_company_id = company.id
    sqlite_session.commit()

    second = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile_read,
            [
                make_query_result(
                    query, items=[make_item(name="Acme", row=2, website="https://example.com/new")]
                )
            ],
        ),
        repository=repository,
    )
    sqlite_session.refresh(candidate)

    assert second.status in {CompanyDiscoveryRunStatus.SUCCEEDED, CompanyDiscoveryRunStatus.PARTIAL}
    assert candidate.candidate_status == status
    assert candidate.first_seen_run_id == first.run_id
    assert candidate.last_seen_run_id == second.run_id


def test_legacy_profile_countries_are_not_written_to_snapshot(sqlite_session: Session) -> None:
    project = Project(name="Legacy countries")
    sqlite_session.add(project)
    sqlite_session.flush()
    legacy_profile = SearchProfile(
        project_id=project.id,
        name="Legacy",
        product_or_service="Service",
        target_customer_types=["firms"],
        countries=["Germany", "France"],
        query_templates=["{target_customer_type} {country}"],
    )
    sqlite_session.add(legacy_profile)
    sqlite_session.flush()
    profile_read = SearchProfileRead.model_validate(legacy_profile)
    query = make_query(profile_read, 1)
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)

    report = run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(profile_read, [make_query_result(query, items=())]),
        repository=repository,
    )

    run = sqlite_session.get(CompanyDiscoveryRun, report.run_id)
    assert run is not None
    assert run.request_snapshot["country_codes"] == []


def test_company_and_contact_rows_are_not_touched(sqlite_session: Session) -> None:
    project = Project(name="Isolation project")
    sqlite_session.add(project)
    sqlite_session.flush()
    profile = make_profile_from_db(sqlite_session, project)
    profile_read = SearchProfileRead.model_validate(profile)
    query = make_query(profile_read, 1)
    preview = SearchQueryPreview(
        profile_id=profile_read.id,
        profile_name=profile_read.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    repository = CompanyDiscoveryStagingRepository(sqlite_session)
    before_company_count = sqlite_session.scalar(select(func.count()).select_from(Company))
    before_contact_count = sqlite_session.scalar(select(func.count()).select_from(Contact))

    run_orchestrator(
        profile=profile_read,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile_read,
            [
                make_query_result(
                    query,
                    items=[make_item(name="Acme", row=1, website="https://www.example.com/path")],
                )
            ],
        ),
        repository=repository,
    )
    sqlite_session.commit()

    assert sqlite_session.scalar(select(func.count()).select_from(Company)) == before_company_count
    assert sqlite_session.scalar(select(func.count()).select_from(Contact)) == before_contact_count


def test_unexpected_persistence_failure_bubbles_without_masking() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )

    case = RepositorySpy(fail_create=RuntimeError("create failed"))
    with pytest.raises(RuntimeError, match="create failed"):
        run_orchestrator(
            profile=profile,
            provider=FakeProvider(),
            dry_run=False,
            preview=preview,
            execution_result=make_dry_result(profile, [make_query_result(query, items=())]),
            repository=case,
        )
    assert case.calls[0][0] == "create_run"
    assert all(call[0] != "update_run" for call in case.calls)


def test_persisted_repository_mutation_order_is_create_upserts_then_update() -> None:
    profile = make_profile_read()
    query_a = make_query(profile, 1, country_code="DE")
    query_b = make_query(profile, 2, country_code="DE")
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query_a, query_b],
    )
    repository = RepositorySpy(
        upsert_results=[
            RepositorySpyUpsertResult(created=True),
            RepositorySpyUpsertResult(created=True),
        ]
    )

    report = run_orchestrator(
        profile=profile,
        provider=FakeProvider(),
        dry_run=False,
        preview=preview,
        execution_result=make_dry_result(
            profile,
            [
                make_query_result(
                    query_a,
                    items=[make_item(name="Acme A", row=1, website="https://a.example.com")],
                ),
                make_query_result(
                    query_b,
                    items=[make_item(name="Acme B", row=2, website="https://b.example.com")],
                ),
            ],
        ),
        repository=repository,
    )

    assert report.status == CompanyDiscoveryRunStatus.SUCCEEDED
    assert len(repository.calls) == 4
    assert repository.calls[0][0] == "create_run"
    assert repository.calls[1][0] == "upsert_candidate"
    assert repository.calls[2][0] == "upsert_candidate"
    assert repository.calls[3][0] == "update_run"
    assert report.candidate_upserts == 2


def test_persistence_failure_on_first_upsert_propagates_without_masking() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    case = RepositorySpy(
        fail_upsert=None,
        fail_upsert_on_call=0,
        upsert_results=[RepositorySpyUpsertResult(created=True)],
    )

    with pytest.raises(RuntimeError, match="upsert failed"):
        run_orchestrator(
            profile=profile,
            provider=FakeProvider(),
            dry_run=False,
            preview=preview,
            execution_result=make_dry_result(
                profile,
                [
                    make_query_result(
                        query,
                        items=[
                            make_item(name="Acme", row=1, website="https://upsert-fail.example.com")
                        ],
                    )
                ],
            ),
            repository=case,
        )

    assert case.calls[0][0] == "create_run"
    assert case.calls[1][0] == "upsert_candidate"
    assert all(call[0] != "update_run" for call in case.calls)


def test_persistence_failure_on_second_upsert_propagates_without_masking() -> None:
    profile = make_profile_read()
    query_a = make_query(profile, 1, country_code="DE")
    query_b = make_query(profile, 2, country_code="DE")
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=2,
        estimated_provider_requests=2,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query_a, query_b],
    )
    case = RepositorySpy(
        fail_upsert=None,
        fail_upsert_on_call=1,
        upsert_results=[
            RepositorySpyUpsertResult(created=True),
            RepositorySpyUpsertResult(updated=True),
        ],
    )

    with pytest.raises(RuntimeError, match="upsert failed"):
        run_orchestrator(
            profile=profile,
            provider=FakeProvider(),
            dry_run=False,
            preview=preview,
            execution_result=make_dry_result(
                profile,
                [
                    make_query_result(
                        query_a,
                        items=[
                            make_item(name="Acme A", row=1, website="https://first.example.com")
                        ],
                    ),
                    make_query_result(
                        query_b,
                        items=[
                            make_item(name="Acme B", row=2, website="https://second.example.com")
                        ],
                    ),
                ],
            ),
            repository=case,
        )

    assert case.calls[0][0] == "create_run"
    assert case.calls[1][0] == "upsert_candidate"
    assert case.calls[2][0] == "upsert_candidate"
    assert all(call[0] != "update_run" for call in case.calls)


def test_persistence_failure_on_update_run_propagates_without_masking() -> None:
    profile = make_profile_read()
    query = make_query(profile, 1)
    preview = SearchQueryPreview(
        profile_id=profile.id,
        profile_name=profile.name,
        query_count=1,
        estimated_provider_requests=1,
        result_limit_per_query=10,
        total_result_ceiling=25,
        queries=[query],
    )
    case = RepositorySpy(
        upsert_results=[RepositorySpyUpsertResult(created=True)],
        fail_update=RuntimeError("update failed"),
    )

    with pytest.raises(RuntimeError, match="update failed"):
        run_orchestrator(
            profile=profile,
            provider=FakeProvider(),
            dry_run=False,
            preview=preview,
            execution_result=make_dry_result(
                profile,
                [
                    make_query_result(
                        query,
                        items=[
                            make_item(name="Acme", row=1, website="https://update-fail.example.com")
                        ],
                    )
                ],
            ),
            repository=case,
        )

    assert case.calls[0][0] == "create_run"
    assert case.calls[1][0] == "upsert_candidate"
    assert case.calls[-1][0] == "update_run"

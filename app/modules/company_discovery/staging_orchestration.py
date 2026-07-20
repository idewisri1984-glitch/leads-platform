from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.modules.company_discovery.models import CompanyDiscoveryRun, CompanyDiscoveryRunStatus
from app.modules.company_discovery.profile_execution import (
    SearchProfileDiscoveryExecutionError,
    SearchProfileDiscoveryService,
)
from app.modules.company_discovery.provider_interfaces import DiscoveryProvider
from app.modules.company_discovery.schemas import (
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryQueryResult,
)
from app.modules.company_discovery.staging_adapter import (
    CompanyDiscoveryStagingAdapterError,
    adapt_query_items,
    candidate_create_from_adapter_payload,
)
from app.modules.company_discovery.staging_normalization import NormalizedCompanyDiscoveryCandidate
from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryRequestSnapshot,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
    CompanyDiscoverySourceMode,
)
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidateDraft,
    CompanyDiscoveryStagingCandidatePreview,
    CompanyDiscoveryStagingRunResult,
)
from app.modules.search_profile.query_generation import SearchProfileQueryGenerator
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQueryPreview,
)


class CompanyDiscoveryStagingServiceError(ValueError):
    """Raised for malformed local orchestration configuration."""


_ALLOWED_ORCHESTRATION_ERROR_CODES = {
    "authentication_error",
    "configuration_error",
    "quota_exceeded",
    "rate_limit_error",
    "request_error",
    "response_error",
    "response_too_large",
    "provider_error",
    "candidate_invalid",
    "execution_invalid",
    "execution_failed",
}

_INVALID_PROVIDER_NAME = "invalid-provider"


class CompanyDiscoveryStagingService:
    """
    Candidate-staging orchestration for search-profile discovery.
    """

    def __init__(
        self,
        repository: CompanyDiscoveryStagingRepository | None = None,
        execution_service: SearchProfileDiscoveryService | None = None,
        query_generator: SearchProfileQueryGenerator | None = None,
    ) -> None:
        self.repository = repository
        self.query_generator = query_generator or SearchProfileQueryGenerator()
        self.execution_service = execution_service or SearchProfileDiscoveryService(
            self.query_generator
        )

    def run(
        self,
        *,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
        dry_run: bool,
        repository: CompanyDiscoveryStagingRepository | None = None,
    ) -> CompanyDiscoveryStagingRunResult:
        effective_repository = repository or self.repository

        if not dry_run and effective_repository is None:
            raise CompanyDiscoveryStagingServiceError(
                "A staging repository is required for persisted runs."
            )

        preview = self.query_generator.generate_preview(profile, options)

        try:
            provider_name = self._resolve_provider_name(provider)
        except BaseException:
            raise

        if provider_name is None:
            return self._failed_run_result(
                profile=profile,
                provider_name=_INVALID_PROVIDER_NAME,
                preview=preview,
                options=options,
                query_count=preview.query_count,
                executed_queries=0,
                successful_queries=0,
                provider_result_count=0,
                provider_error_count=0,
                dry_run=dry_run,
                repository=effective_repository,
                error_code="configuration_error",
                existing_adapter_error_count=0,
                rejected_candidate_count=0,
                duplicate_candidate_count=0,
            )

        try:
            execution_result = self.execution_service.run_dry(profile, provider, options)
        except SearchProfileDiscoveryExecutionError:
            raise
        except Exception:
            # Unexpected execution failures are converted to deterministic orchestration result.
            return self._failed_run_result(
                profile=profile,
                provider_name=provider_name,
                preview=preview,
                options=options,
                query_count=preview.query_count,
                executed_queries=0,
                successful_queries=0,
                provider_result_count=0,
                provider_error_count=0,
                dry_run=dry_run,
                repository=effective_repository,
                error_code="execution_failed",
                existing_adapter_error_count=0,
                rejected_candidate_count=0,
                duplicate_candidate_count=0,
            )

        try:
            execution_result = SearchProfileDiscoveryDryRunResult.model_validate(
                execution_result.model_dump()
            )
        except Exception:
            # Malformed execution output is treated as a deterministic failed result.
            return self._failed_run_result(
                profile=profile,
                provider_name=provider_name,
                preview=preview,
                options=options,
                query_count=preview.query_count,
                executed_queries=0,
                successful_queries=0,
                provider_result_count=0,
                provider_error_count=0,
                dry_run=dry_run,
                repository=effective_repository,
                error_code="execution_invalid",
                existing_adapter_error_count=0,
                rejected_candidate_count=0,
                duplicate_candidate_count=0,
            )

        try:
            self._validate_execution_result(profile, provider_name, preview, execution_result)
            valid_execution_result = True
        except ValueError:
            valid_execution_result = False

        if valid_execution_result:
            try:
                adapter_rows, rejected_candidate_count = self._adapt_and_deduplicate_candidates(
                    project_id=profile.project_id,
                    provider=provider_name,
                    query_results=execution_result.query_results,
                )
            except Exception:
                # Unexpected per-item corruption should fail safely with zero upsert.
                valid_execution_result = False
                adapter_rows = OrderedDict()
                rejected_candidate_count = 0
                duplicate_candidate_count = 0
        else:
            adapter_rows = OrderedDict()
            rejected_candidate_count = 0
            duplicate_candidate_count = 0

        if not valid_execution_result:
            return self._failed_run_result(
                profile=profile,
                provider_name=provider_name,
                preview=preview,
                options=options,
                query_count=preview.query_count,
                executed_queries=0,
                successful_queries=0,
                provider_result_count=0,
                provider_error_count=0,
                dry_run=dry_run,
                repository=effective_repository,
                error_code="execution_invalid",
                existing_adapter_error_count=0,
                rejected_candidate_count=0,
                duplicate_candidate_count=0,
            )

        duplicate_candidate_count = sum(row.duplicates for row in adapter_rows.values())
        unique_candidate_count = len(adapter_rows)
        status, error_code = self._evaluate_status_and_error_code(
            execution_result=execution_result,
            unique_candidate_count=unique_candidate_count,
            candidate_rejections=rejected_candidate_count,
            valid_execution_result=valid_execution_result,
        )

        successful_queries = sum(
            1
            for query_result in execution_result.query_results
            if query_result.provider_error is None
        )

        if dry_run:
            return self._build_result(
                project_id=profile.project_id,
                search_profile_id=profile.id,
                profile_name=profile.name,
                provider=provider_name,
                dry_run=True,
                status=status,
                request_fingerprint=self._build_snapshot(profile, options, preview).fingerprint(),
                query_count=execution_result.query_count,
                executed_queries=execution_result.executed_queries,
                successful_queries=successful_queries,
                provider_result_count=execution_result.total_provider_results,
                provider_error_count=execution_result.total_provider_errors,
                existing_adapter_error_count=execution_result.total_adapter_errors,
                rejected_candidate_count=rejected_candidate_count,
                duplicate_candidate_count=duplicate_candidate_count,
                unique_candidate_count=unique_candidate_count,
                candidate_upserts=0,
                candidates_created=0,
                candidates_updated=0,
                candidates_protected=0,
                stopped_early=execution_result.stopped_early,
                stop_reason=execution_result.stop_reason,
                error_code=error_code,
                candidates=[self._candidate_preview(item) for item in adapter_rows.values()],
            )

        assert effective_repository is not None

        run = self._create_run(
            repository=effective_repository,
            profile=profile,
            provider_name=provider_name,
            snapshot=self._build_snapshot(profile, options, preview),
            query_count=execution_result.query_count,
            result_count=execution_result.total_provider_results,
        )

        candidate_upserts = 0
        candidates_created = 0
        candidates_updated = 0
        candidates_protected = 0

        if status in (CompanyDiscoveryRunStatus.SUCCEEDED, CompanyDiscoveryRunStatus.PARTIAL):
            for row in adapter_rows.values():
                row_best_position = row.best_position
                if row_best_position is not None:
                    row.draft = row.draft.model_copy(update={"position": row_best_position})
                upsert_result = effective_repository.upsert_candidate(
                    project_id=profile.project_id,
                    run_id=run.id,
                    data=candidate_create_from_adapter_payload(
                        draft=row.draft,
                        run_id=run.id,
                        normalized=row.normalized,
                    ),
                )
                candidate_upserts += 1
                candidates_created += 1 if upsert_result.created else 0
                candidates_updated += 1 if upsert_result.updated else 0
                candidates_protected += 1 if upsert_result.protected else 0

        completed_at = datetime.now(UTC)
        candidate_count = (
            candidate_upserts
            if status in (CompanyDiscoveryRunStatus.SUCCEEDED, CompanyDiscoveryRunStatus.PARTIAL)
            else 0
        )

        effective_repository.update_run(
            run.id,
            CompanyDiscoveryRunUpdate(
                run_status=status,
                query_count=execution_result.query_count,
                result_count=execution_result.total_provider_results,
                candidate_count=candidate_count,
                completed_at=completed_at,
                error_code=error_code,
            ),
        )

        return self._build_result(
            project_id=profile.project_id,
            search_profile_id=profile.id,
            profile_name=profile.name,
            provider=provider_name,
            dry_run=False,
            status=status,
            request_fingerprint=run.request_fingerprint,
            query_count=execution_result.query_count,
            executed_queries=execution_result.executed_queries,
            successful_queries=successful_queries,
            provider_result_count=execution_result.total_provider_results,
            provider_error_count=execution_result.total_provider_errors,
            existing_adapter_error_count=execution_result.total_adapter_errors,
            rejected_candidate_count=rejected_candidate_count,
            duplicate_candidate_count=duplicate_candidate_count,
            unique_candidate_count=unique_candidate_count,
            candidate_upserts=candidate_upserts,
            candidates_created=candidates_created,
            candidates_updated=candidates_updated,
            candidates_protected=candidates_protected,
            stopped_early=execution_result.stopped_early,
            stop_reason=execution_result.stop_reason,
            error_code=error_code,
            run_id=run.id,
            run_persisted=True,
            completed_at=completed_at,
            candidates=[self._candidate_preview(item) for item in adapter_rows.values()],
        )

    def _resolve_provider_name(self, provider: DiscoveryProvider) -> str | None:
        try:
            raw_provider_name = provider.provider_name
        except Exception:
            return None

        if not isinstance(raw_provider_name, str):
            return None

        normalized = " ".join(raw_provider_name.strip().split())
        if not normalized:
            return None
        if "<" in normalized or ">" in normalized:
            return None
        return normalized

    def _failed_run_result(
        self,
        *,
        profile: SearchProfileRead,
        provider_name: str,
        preview: SearchQueryPreview,
        options: SearchProfileRunOptions | None,
        query_count: int,
        executed_queries: int,
        successful_queries: int,
        provider_result_count: int,
        provider_error_count: int,
        dry_run: bool,
        repository: CompanyDiscoveryStagingRepository | None,
        error_code: str,
        existing_adapter_error_count: int,
        rejected_candidate_count: int,
        duplicate_candidate_count: int,
    ) -> CompanyDiscoveryStagingRunResult:
        request_fingerprint = self._build_snapshot(profile, options, preview).fingerprint()

        if dry_run:
            return self._build_result(
                project_id=profile.project_id,
                search_profile_id=profile.id,
                profile_name=profile.name,
                provider=provider_name,
                dry_run=True,
                status=CompanyDiscoveryRunStatus.FAILED,
                request_fingerprint=request_fingerprint,
                query_count=query_count,
                executed_queries=executed_queries,
                successful_queries=successful_queries,
                provider_result_count=provider_result_count,
                provider_error_count=provider_error_count,
                existing_adapter_error_count=existing_adapter_error_count,
                rejected_candidate_count=rejected_candidate_count,
                duplicate_candidate_count=duplicate_candidate_count,
                unique_candidate_count=0,
                candidate_upserts=0,
                candidates_created=0,
                candidates_updated=0,
                candidates_protected=0,
                stopped_early=False,
                stop_reason=None,
                error_code=error_code,
                run_id=None,
                run_persisted=False,
                candidates=[],
            )

        if repository is None:
            raise CompanyDiscoveryStagingServiceError(
                "A staging repository is required for failed persisted runs."
            )

        run = self._create_run(
            repository=repository,
            profile=profile,
            provider_name=provider_name,
            snapshot=self._build_snapshot(profile, options, preview),
            query_count=query_count,
            result_count=provider_result_count,
            initial_error_code=error_code,
        )
        completed_at = datetime.now(UTC)
        repository.update_run(
            run.id,
            CompanyDiscoveryRunUpdate(
                run_status=CompanyDiscoveryRunStatus.FAILED,
                query_count=query_count,
                result_count=provider_result_count,
                candidate_count=0,
                completed_at=completed_at,
                error_code=error_code,
            ),
        )

        return self._build_result(
            project_id=profile.project_id,
            search_profile_id=profile.id,
            profile_name=profile.name,
            provider=provider_name,
            dry_run=False,
            status=CompanyDiscoveryRunStatus.FAILED,
            request_fingerprint=run.request_fingerprint,
            query_count=query_count,
            executed_queries=executed_queries,
            successful_queries=successful_queries,
            provider_result_count=provider_result_count,
            provider_error_count=provider_error_count,
            existing_adapter_error_count=existing_adapter_error_count,
            rejected_candidate_count=rejected_candidate_count,
            duplicate_candidate_count=duplicate_candidate_count,
            unique_candidate_count=0,
            candidate_upserts=0,
            candidates_created=0,
            candidates_updated=0,
            candidates_protected=0,
            stopped_early=False,
            stop_reason=None,
            error_code=error_code,
            run_id=run.id,
            run_persisted=True,
            completed_at=completed_at,
            candidates=[],
        )

    def _validate_execution_result(
        self,
        profile: SearchProfileRead,
        provider_name: str,
        preview: SearchQueryPreview,
        result: SearchProfileDiscoveryDryRunResult,
    ) -> None:
        if profile.id != result.profile_id:
            raise ValueError("Profile ID mismatch.")
        if profile.name != result.profile_name:
            raise ValueError("Profile name mismatch.")
        if provider_name != result.provider:
            raise ValueError("Provider mismatch.")
        if preview.query_count != result.query_count:
            raise ValueError("query_count does not match preview.")
        if preview.estimated_provider_requests != result.estimated_provider_requests:
            raise ValueError("estimated_provider_requests does not match preview.")
        if preview.total_result_ceiling != result.total_result_ceiling:
            raise ValueError("total_result_ceiling does not match preview.")
        if result.executed_queries != len(result.query_results):
            raise ValueError("execution_result counters are invalid.")
        if result.executed_queries > result.query_count:
            raise ValueError("Executed query count cannot exceed planned count.")
        if len(result.query_results) > len(preview.queries):
            raise ValueError("Execution returned extra query results.")
        if result.stopped_early and result.stop_reason is None:
            raise ValueError("stop_reason is required when stopped_early is true.")
        if not result.stopped_early and result.stop_reason is not None:
            raise ValueError("stop_reason must be absent when stopped_early is false.")

        for index, query_result in enumerate(result.query_results):
            if query_result.query != preview.queries[index]:
                raise ValueError("Query results are not the planned prefix.")
            if query_result.query.profile_id != profile.id:
                raise ValueError("Query result is for another profile.")
            if query_result.query.profile_name != profile.name:
                raise ValueError("Query result profile name does not match profile.")
            if query_result.provider != provider_name:
                raise ValueError("Query result provider mismatch.")

    def _adapt_and_deduplicate_candidates(
        self,
        *,
        project_id: int,
        provider: str,
        query_results: Sequence[SearchProfileDiscoveryQueryResult],
    ) -> tuple[OrderedDict[str, "_CandidateRow"], int]:
        rows: OrderedDict[str, _CandidateRow] = OrderedDict()
        total_rejections = 0

        for query_result in query_results:
            try:
                row_candidates, rejected = adapt_query_items(
                    project_id=project_id,
                    provider=provider,
                    query=query_result.query,
                    items=query_result.items,
                )
                total_rejections += rejected
            except Exception:
                raise CompanyDiscoveryStagingAdapterError(
                    "Company discovery candidate could not be staged."
                ) from None

            for draft, normalized in row_candidates:
                existing = rows.get(normalized.identity_key)
                if existing is None:
                    rows[normalized.identity_key] = _CandidateRow(
                        identity_key=normalized.identity_key,
                        draft=draft,
                        normalized=normalized,
                        duplicates=0,
                        best_position=draft.position,
                    )
                    continue

                existing.duplicates += 1
                existing.normalized = self._fill_missing_fields(existing.normalized, normalized)
                existing.best_position = self._best_position(existing.best_position, draft.position)
                if existing.best_position is not None:
                    existing.draft = existing.draft.model_copy(
                        update={"position": existing.best_position}
                    )

        return rows, total_rejections

    def _best_position(self, current: int | None, candidate: int | None) -> int | None:
        if candidate is None:
            return current
        if current is None:
            return candidate
        return candidate if candidate < current else current

    def _fill_missing_fields(
        self,
        current: NormalizedCompanyDiscoveryCandidate,
        incoming: NormalizedCompanyDiscoveryCandidate,
    ) -> NormalizedCompanyDiscoveryCandidate:
        return NormalizedCompanyDiscoveryCandidate(
            name=current.name if current.name is not None else incoming.name,
            normalized_name=current.normalized_name
            if current.normalized_name is not None
            else incoming.normalized_name,
            website=current.website if current.website is not None else incoming.website,
            website_identity=(
                current.website_identity
                if current.website_identity is not None
                else incoming.website_identity
            ),
            country_code=current.country_code
            if current.country_code is not None
            else incoming.country_code,
            identity_key=current.identity_key,
        )

    def _build_snapshot(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions | None,
        preview: SearchQueryPreview,
    ) -> CompanyDiscoveryRequestSnapshot:
        country_codes: tuple[str, ...] = ()
        if options is not None and options.country_codes is not None:
            country_codes = options.country_codes

        return CompanyDiscoveryRequestSnapshot(
            source_mode=CompanyDiscoverySourceMode.SEARCH_PROFILE,
            search_profile_id=profile.id,
            country_codes=country_codes,
            query_count=preview.query_count,
            result_limit=preview.result_limit_per_query,
            total_result_ceiling=preview.total_result_ceiling,
        )

    def _evaluate_status_and_error_code(
        self,
        *,
        execution_result: SearchProfileDiscoveryDryRunResult,
        unique_candidate_count: int,
        candidate_rejections: int,
        valid_execution_result: bool,
    ) -> tuple[CompanyDiscoveryRunStatus, str | None]:
        first_provider_error = self._first_provider_error_code(execution_result)

        successful_query_count = sum(
            1
            for query_result in execution_result.query_results
            if query_result.provider_error is None
        )
        provider_error_count = execution_result.total_provider_errors
        adapter_error_count = execution_result.total_adapter_errors
        stopped_early = execution_result.stopped_early

        if not valid_execution_result:
            return CompanyDiscoveryRunStatus.FAILED, "execution_invalid"

        if execution_result.query_count == 0:
            return CompanyDiscoveryRunStatus.NOT_FOUND, None

        if successful_query_count == 0:
            if provider_error_count > 0:
                return CompanyDiscoveryRunStatus.FAILED, first_provider_error or "execution_failed"
            return CompanyDiscoveryRunStatus.FAILED, "execution_failed"

        if unique_candidate_count > 0:
            if (
                provider_error_count == 0
                and adapter_error_count == 0
                and candidate_rejections == 0
                and not stopped_early
            ):
                return CompanyDiscoveryRunStatus.SUCCEEDED, None
            if provider_error_count > 0:
                return CompanyDiscoveryRunStatus.PARTIAL, first_provider_error
            if stopped_early:
                return (
                    CompanyDiscoveryRunStatus.PARTIAL,
                    execution_result.stop_reason or "execution_failed",
                )
            return CompanyDiscoveryRunStatus.PARTIAL, "candidate_invalid"

        if provider_error_count > 0:
            return CompanyDiscoveryRunStatus.PARTIAL, first_provider_error

        if stopped_early:
            return (
                CompanyDiscoveryRunStatus.PARTIAL,
                execution_result.stop_reason or "execution_failed",
            )

        if adapter_error_count > 0 or candidate_rejections > 0:
            return CompanyDiscoveryRunStatus.PARTIAL, "candidate_invalid"

        return CompanyDiscoveryRunStatus.NOT_FOUND, None

    def _first_provider_error_code(self, result: SearchProfileDiscoveryDryRunResult) -> str | None:
        for query_result in result.query_results:
            if query_result.provider_error is not None:
                return query_result.provider_error.code
        return None

    def _create_run(
        self,
        *,
        repository: CompanyDiscoveryStagingRepository,
        profile: SearchProfileRead,
        provider_name: str,
        snapshot: CompanyDiscoveryRequestSnapshot,
        query_count: int,
        result_count: int,
        initial_error_code: str | None = None,
    ) -> CompanyDiscoveryRun:
        if (
            initial_error_code is not None
            and initial_error_code not in _ALLOWED_ORCHESTRATION_ERROR_CODES
        ):
            raise CompanyDiscoveryStagingServiceError("Invalid orchestration error code.")

        return repository.create_run(
            CompanyDiscoveryRunCreate(
                project_id=profile.project_id,
                search_profile_id=profile.id,
                provider=provider_name,
                request_snapshot=snapshot,
                run_status=CompanyDiscoveryRunStatus.PENDING,
                query_count=query_count,
                result_count=result_count,
                candidate_count=0,
                error_code=initial_error_code,
            )
        )

    def _candidate_preview(
        self,
        row: "_CandidateRow",
    ) -> CompanyDiscoveryStagingCandidatePreview:
        return CompanyDiscoveryStagingCandidatePreview(
            name=row.normalized.name,
            website=row.normalized.website,
            website_identity=row.normalized.website_identity,
            country_code=row.normalized.country_code,
            best_position=row.best_position,
            identity_key=row.normalized.identity_key,
        )

    def _build_result(
        self,
        *,
        project_id: int,
        search_profile_id: int,
        profile_name: str,
        provider: str,
        dry_run: bool,
        status: CompanyDiscoveryRunStatus,
        request_fingerprint: str,
        query_count: int,
        executed_queries: int,
        successful_queries: int,
        provider_result_count: int,
        provider_error_count: int,
        existing_adapter_error_count: int,
        rejected_candidate_count: int,
        duplicate_candidate_count: int,
        unique_candidate_count: int,
        candidate_upserts: int,
        candidates_created: int,
        candidates_updated: int,
        candidates_protected: int,
        stopped_early: bool,
        stop_reason: str | None,
        error_code: str | None,
        candidates: list[CompanyDiscoveryStagingCandidatePreview],
        run_id: int | None = None,
        run_persisted: bool = False,
        completed_at: datetime | None = None,
    ) -> CompanyDiscoveryStagingRunResult:
        if error_code is not None and error_code not in _ALLOWED_ORCHESTRATION_ERROR_CODES:
            raise CompanyDiscoveryStagingServiceError("Invalid orchestration error code.")

        return CompanyDiscoveryStagingRunResult(
            project_id=project_id,
            search_profile_id=search_profile_id,
            profile_name=profile_name,
            provider=provider,
            dry_run=dry_run,
            status=status,
            request_fingerprint=request_fingerprint,
            query_count=query_count,
            executed_queries=executed_queries,
            successful_queries=successful_queries,
            provider_result_count=provider_result_count,
            provider_error_count=provider_error_count,
            existing_adapter_error_count=existing_adapter_error_count,
            rejected_candidate_count=rejected_candidate_count,
            duplicate_candidate_count=duplicate_candidate_count,
            unique_candidate_count=unique_candidate_count,
            candidate_upserts=candidate_upserts,
            candidates_created=candidates_created,
            candidates_updated=candidates_updated,
            candidates_protected=candidates_protected,
            run_id=run_id,
            run_persisted=run_persisted,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            error_code=error_code,
            candidates=candidates,
            completed_at=completed_at,
        )


@dataclass
class _CandidateRow:
    identity_key: str
    draft: CompanyDiscoveryStagingCandidateDraft
    normalized: NormalizedCompanyDiscoveryCandidate
    duplicates: int
    best_position: int | None = None

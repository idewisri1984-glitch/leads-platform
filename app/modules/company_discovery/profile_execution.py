from app.modules.company_discovery.provider_interfaces import (
    DiscoveryProvider,
    DiscoveryProviderConfigurationError,
    DiscoveryProviderError,
    DiscoveryProviderRateLimitError,
    DiscoveryProviderRequestError,
    DiscoveryProviderResponseError,
)
from app.modules.company_discovery.result_adapter import (
    DiscoveryResultAdapterError,
    provider_result_to_ingestion_item,
)
from app.modules.company_discovery.schemas import (
    ProviderErrorCode,
    SearchProfileDiscoveryAdapterError,
    SearchProfileDiscoveryDryRunResult,
    SearchProfileDiscoveryProviderError,
    SearchProfileDiscoveryQueryResult,
    StopReason,
)
from app.modules.company_import.schemas import CompanyIngestionItem
from app.modules.search_profile.query_generation import SearchProfileQueryGenerator
from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)

_PROVIDER_ERROR_MESSAGES: dict[ProviderErrorCode, str] = {
    "configuration_error": "Discovery provider is not configured.",
    "rate_limit_error": "Discovery provider rate limit exceeded.",
    "request_error": "Discovery provider request failed.",
    "response_error": "Discovery provider response was invalid.",
    "provider_error": "Discovery provider failed.",
}
_ADAPTER_ERROR_MESSAGE = "Discovery result could not be adapted."


class SearchProfileDiscoveryExecutionError(Exception):
    """Controlled dry-run execution failure."""


class SearchProfileDiscoveryService:
    """
    Execute bounded search profile discovery without persistence.
    """

    def __init__(
        self,
        query_generator: SearchProfileQueryGenerator | None = None,
    ) -> None:
        self.query_generator = query_generator or SearchProfileQueryGenerator()

    def run_dry(
        self,
        profile: SearchProfileRead,
        provider: DiscoveryProvider,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchProfileDiscoveryDryRunResult:
        if not profile.enabled:
            raise SearchProfileDiscoveryExecutionError("Search profile is disabled.")

        provider_name_value = provider.provider_name

        if not isinstance(provider_name_value, str) or not provider_name_value.strip():
            raise SearchProfileDiscoveryExecutionError("Discovery provider name is invalid.")

        provider_name = provider_name_value.strip()
        preview = self.query_generator.generate_preview(profile, options)
        query_results: list[SearchProfileDiscoveryQueryResult] = []
        stopped_early = False
        stop_reason: StopReason | None = None

        for query in preview.queries:
            try:
                response = provider.search(query)
            except DiscoveryProviderConfigurationError:
                query_results.append(
                    self._provider_error_result(query, provider_name, "configuration_error")
                )
                stopped_early = True
                stop_reason = "configuration_error"
                break
            except DiscoveryProviderRateLimitError:
                query_results.append(
                    self._provider_error_result(query, provider_name, "rate_limit_error")
                )
                stopped_early = True
                stop_reason = "rate_limit_error"
                break
            except DiscoveryProviderRequestError:
                query_results.append(
                    self._provider_error_result(query, provider_name, "request_error")
                )
                continue
            except DiscoveryProviderResponseError:
                query_results.append(
                    self._provider_error_result(query, provider_name, "response_error")
                )
                continue
            except DiscoveryProviderError:
                query_results.append(
                    self._provider_error_result(query, provider_name, "provider_error")
                )
                stopped_early = True
                stop_reason = "provider_error"
                break

            items: list[CompanyIngestionItem] = []
            adapter_errors: list[SearchProfileDiscoveryAdapterError] = []

            for result in response.results:
                try:
                    items.append(
                        provider_result_to_ingestion_item(
                            result,
                            query=query,
                            provider_name=provider_name,
                        )
                    )
                except DiscoveryResultAdapterError:
                    adapter_errors.append(
                        SearchProfileDiscoveryAdapterError(
                            position=self._safe_position(result.position),
                            message=_ADAPTER_ERROR_MESSAGE,
                        )
                    )

            query_results.append(
                SearchProfileDiscoveryQueryResult(
                    query=query,
                    provider=provider_name,
                    provider_result_count=len(response.results),
                    adapted_item_count=len(items),
                    adapter_error_count=len(adapter_errors),
                    items=items,
                    adapter_errors=adapter_errors,
                )
            )

        return self._dry_run_result(
            preview=preview,
            provider_name=provider_name,
            query_results=query_results,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
        )

    def _provider_error_result(
        self,
        query: SearchQuery,
        provider_name: str,
        code: ProviderErrorCode,
    ) -> SearchProfileDiscoveryQueryResult:
        return SearchProfileDiscoveryQueryResult(
            query=query,
            provider=provider_name,
            provider_result_count=0,
            adapted_item_count=0,
            adapter_error_count=0,
            provider_error=SearchProfileDiscoveryProviderError(
                code=code,
                message=_PROVIDER_ERROR_MESSAGES[code],
            ),
        )

    def _safe_position(self, value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None

        return value

    def _dry_run_result(
        self,
        *,
        preview: SearchQueryPreview,
        provider_name: str,
        query_results: list[SearchProfileDiscoveryQueryResult],
        stopped_early: bool,
        stop_reason: StopReason | None,
    ) -> SearchProfileDiscoveryDryRunResult:
        return SearchProfileDiscoveryDryRunResult(
            profile_id=preview.profile_id,
            profile_name=preview.profile_name,
            provider=provider_name,
            query_count=preview.query_count,
            estimated_provider_requests=preview.estimated_provider_requests,
            executed_queries=len(query_results),
            total_provider_results=sum(result.provider_result_count for result in query_results),
            total_adapted_items=sum(result.adapted_item_count for result in query_results),
            total_adapter_errors=sum(result.adapter_error_count for result in query_results),
            total_provider_errors=sum(
                result.provider_error is not None for result in query_results
            ),
            total_result_ceiling=preview.total_result_ceiling,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            query_results=query_results,
        )

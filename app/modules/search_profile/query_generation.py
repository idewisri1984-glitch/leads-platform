import re
from collections.abc import Iterable

from app.modules.search_profile.schemas import (
    SearchProfileRead,
    SearchProfileRunOptions,
    SearchQuery,
    SearchQueryPreview,
)

DEFAULT_QUERY_TEMPLATES = [
    "{target_customer_type} {city} {country}",
    "{target_customer_type} {country}",
    "{target_industry} companies {city} {country}",
    "{target_industry} companies {country}",
    "{positive_keyword} {city} {country}",
    "{positive_keyword} {country}",
]

_SUPPORTED_PLACEHOLDERS = {
    "product_or_service",
    "target_customer_type",
    "target_industry",
    "positive_keyword",
    "country",
    "city",
    "language",
}
_AUDIENCE_PLACEHOLDERS = {
    "target_customer_type",
    "target_industry",
    "positive_keyword",
}
_PLACEHOLDER_PATTERN = re.compile(r"{([^{}]+)}")


class SearchProfileQueryGenerationError(ValueError):
    """
    Search profile query templates cannot be safely rendered.
    """


type GeographyTarget = tuple[str | None, str | None]


class SearchProfileQueryGenerator:
    """
    Deterministic provider-independent search profile query generator.
    """

    def generate_preview(
        self,
        profile: SearchProfileRead,
        options: SearchProfileRunOptions | None = None,
    ) -> SearchQueryPreview:
        run_options = options or SearchProfileRunOptions()
        effective_max_queries = self._effective_limit(
            run_options.max_queries,
            profile.max_queries_per_run,
        )
        effective_result_limit = self._effective_limit(
            run_options.result_limit_per_query,
            profile.result_limit,
        )
        effective_total_result_ceiling = self._effective_limit(
            run_options.total_result_ceiling,
            profile.total_result_ceiling,
        )

        queries: list[SearchQuery] = []
        seen_queries: set[str] = set()
        remaining_result_budget = effective_total_result_ceiling
        templates = profile.query_templates or DEFAULT_QUERY_TEMPLATES
        geography_targets = self._geography_targets(profile)
        negative_tokens = self._negative_tokens(profile.negative_keywords)

        for template in templates:
            placeholders = self._template_placeholders(template)
            audience_placeholder = self._audience_placeholder(template, placeholders)
            audience_values = self._audience_values(profile, audience_placeholder)
            language_values = profile.languages if "language" in placeholders else [None]

            for audience_value in audience_values:
                for city, country in geography_targets:
                    for language in language_values:
                        rendered = self._render_template(
                            template=template,
                            profile=profile,
                            audience_placeholder=audience_placeholder,
                            audience_value=audience_value,
                            city=city,
                            country=country,
                            language=language,
                        )

                        if not rendered:
                            continue

                        query_text = self._append_negative_tokens(rendered, negative_tokens)
                        dedupe_key = query_text.casefold()

                        if dedupe_key in seen_queries:
                            continue

                        if remaining_result_budget <= 0:
                            return self._preview(
                                profile=profile,
                                queries=queries,
                                result_limit=effective_result_limit,
                                total_result_ceiling=effective_total_result_ceiling,
                            )

                        query_limit = min(effective_result_limit, remaining_result_budget)
                        seen_queries.add(dedupe_key)
                        queries.append(
                            SearchQuery(
                                text=query_text,
                                profile_id=profile.id,
                                profile_name=profile.name,
                                language=language,
                                country=country,
                                city=city,
                                source_template=template,
                                limit=query_limit,
                            )
                        )
                        remaining_result_budget -= query_limit

                        if len(queries) >= effective_max_queries or remaining_result_budget <= 0:
                            return self._preview(
                                profile=profile,
                                queries=queries,
                                result_limit=effective_result_limit,
                                total_result_ceiling=effective_total_result_ceiling,
                            )

        return self._preview(
            profile=profile,
            queries=queries,
            result_limit=effective_result_limit,
            total_result_ceiling=effective_total_result_ceiling,
        )

    def _effective_limit(self, option_value: int | None, profile_value: int) -> int:
        if option_value is None:
            return profile_value

        return min(option_value, profile_value)

    def _template_placeholders(self, template: str) -> set[str]:
        placeholders = set(_PLACEHOLDER_PATTERN.findall(template))
        unknown_placeholders = placeholders - _SUPPORTED_PLACEHOLDERS

        if unknown_placeholders:
            unknown = ", ".join(sorted(unknown_placeholders))
            raise SearchProfileQueryGenerationError(
                f"Unsupported search query template placeholder: {unknown}."
            )

        return placeholders

    def _audience_placeholder(self, template: str, placeholders: set[str]) -> str | None:
        audience_placeholders = placeholders & _AUDIENCE_PLACEHOLDERS

        if len(audience_placeholders) > 1:
            raise SearchProfileQueryGenerationError(
                f"Search query template uses multiple audience placeholders: {template}."
            )

        if not audience_placeholders:
            return None

        return next(iter(audience_placeholders))

    def _audience_values(
        self,
        profile: SearchProfileRead,
        audience_placeholder: str | None,
    ) -> list[str | None]:
        if audience_placeholder is None:
            return [None]

        if audience_placeholder == "target_customer_type":
            return list(profile.target_customer_types)

        if audience_placeholder == "target_industry":
            return list(profile.target_industries)

        if audience_placeholder == "positive_keyword":
            return list(profile.positive_keywords)

        return []

    def _geography_targets(self, profile: SearchProfileRead) -> list[GeographyTarget]:
        if profile.cities and profile.countries:
            return [(city, country) for city in profile.cities for country in profile.countries]

        if profile.cities:
            return [(city, None) for city in profile.cities]

        if profile.countries:
            return [(None, country) for country in profile.countries]

        return [(None, None)]

    def _negative_tokens(self, negative_keywords: Iterable[str]) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()

        for keyword in negative_keywords:
            normalized = self._normalize_whitespace(keyword)

            if not normalized:
                continue

            dedupe_key = normalized.casefold()

            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)

            if " " in normalized:
                tokens.append(f'-"{normalized}"')
            else:
                tokens.append(f"-{normalized}")

        return tokens

    def _render_template(
        self,
        *,
        template: str,
        profile: SearchProfileRead,
        audience_placeholder: str | None,
        audience_value: str | None,
        city: str | None,
        country: str | None,
        language: str | None,
    ) -> str:
        values = {
            "product_or_service": profile.product_or_service,
            "target_customer_type": "",
            "target_industry": "",
            "positive_keyword": "",
            "country": country or "",
            "city": city or "",
            "language": language or "",
        }

        if audience_placeholder is not None:
            values[audience_placeholder] = audience_value or ""

        rendered = template

        for placeholder in _SUPPORTED_PLACEHOLDERS:
            rendered = rendered.replace(f"{{{placeholder}}}", values[placeholder])

        return self._normalize_whitespace(rendered)

    def _append_negative_tokens(self, query_text: str, negative_tokens: list[str]) -> str:
        if not negative_tokens:
            return query_text

        return self._normalize_whitespace(" ".join([query_text, *negative_tokens]))

    def _normalize_whitespace(self, value: str) -> str:
        return " ".join(value.strip().split())

    def _preview(
        self,
        *,
        profile: SearchProfileRead,
        queries: list[SearchQuery],
        result_limit: int,
        total_result_ceiling: int,
    ) -> SearchQueryPreview:
        return SearchQueryPreview(
            profile_id=profile.id,
            profile_name=profile.name,
            query_count=len(queries),
            estimated_provider_requests=len(queries),
            result_limit_per_query=result_limit,
            total_result_ceiling=total_result_ceiling,
            queries=queries,
        )

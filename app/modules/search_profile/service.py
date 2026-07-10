from typing import Any

from app.modules.search_profile.models import SearchProfile
from app.modules.search_profile.repository import SearchProfileRepository
from app.modules.search_profile.schemas import (
    SearchProfileCreate,
    SearchProfileRead,
    SearchProfileUpdate,
)


class SearchProfileService:
    """
    Search profile business logic.
    """

    def __init__(self, repository: SearchProfileRepository) -> None:
        self.repository = repository

    def create(self, data: SearchProfileCreate) -> SearchProfileRead:
        search_profile = SearchProfile(
            project_id=data.project_id,
            name=data.name,
            description=data.description,
            product_or_service=data.product_or_service,
            target_customer_types=data.target_customer_types,
            target_industries=data.target_industries,
            positive_keywords=data.positive_keywords,
            negative_keywords=data.negative_keywords,
            countries=data.countries,
            cities=data.cities,
            languages=data.languages,
            query_templates=data.query_templates,
            result_limit=data.result_limit,
            max_queries_per_run=data.max_queries_per_run,
            total_result_ceiling=data.total_result_ceiling,
            enabled=data.enabled,
        )

        search_profile = self.repository.create(search_profile)

        return SearchProfileRead.model_validate(search_profile)

    def get(self, profile_id: int) -> SearchProfileRead | None:
        search_profile = self.repository.get(profile_id)

        if search_profile is None:
            return None

        return SearchProfileRead.model_validate(search_profile)

    def get_all(self) -> list[SearchProfileRead]:
        search_profiles = self.repository.get_all()

        return [
            SearchProfileRead.model_validate(search_profile) for search_profile in search_profiles
        ]

    def get_by_project(self, project_id: int) -> list[SearchProfileRead]:
        search_profiles = self.repository.get_by_project(project_id)

        return [
            SearchProfileRead.model_validate(search_profile) for search_profile in search_profiles
        ]

    def update(
        self,
        profile_id: int,
        data: SearchProfileUpdate,
    ) -> SearchProfileRead | None:
        search_profile = self.repository.get(profile_id)

        if search_profile is None:
            return None

        supplied_values = data.supplied_values()
        final_values = self._model_values(search_profile)
        final_values.update(supplied_values)
        validated_final = SearchProfileCreate.model_validate(final_values)

        for field_name in supplied_values:
            setattr(search_profile, field_name, getattr(validated_final, field_name))

        search_profile = self.repository.update(search_profile)

        return SearchProfileRead.model_validate(search_profile)

    def delete(self, profile_id: int) -> bool:
        search_profile = self.repository.get(profile_id)

        if search_profile is None:
            return False

        self.repository.delete(search_profile)

        return True

    def _model_values(self, search_profile: SearchProfile) -> dict[str, Any]:
        return {
            "project_id": search_profile.project_id,
            "name": search_profile.name,
            "description": search_profile.description,
            "product_or_service": search_profile.product_or_service,
            "target_customer_types": search_profile.target_customer_types,
            "target_industries": search_profile.target_industries,
            "positive_keywords": search_profile.positive_keywords,
            "negative_keywords": search_profile.negative_keywords,
            "countries": search_profile.countries,
            "cities": search_profile.cities,
            "languages": search_profile.languages,
            "query_templates": search_profile.query_templates,
            "result_limit": search_profile.result_limit,
            "max_queries_per_run": search_profile.max_queries_per_run,
            "total_result_ceiling": search_profile.total_result_ceiling,
            "enabled": search_profile.enabled,
        }

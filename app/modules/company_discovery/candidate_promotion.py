from typing import Protocol, cast

from app.modules.company_discovery.candidate_promotion_schemas import (
    CompanyDiscoveryCandidatePromotionResult,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus
from app.modules.company_discovery.staging_normalization import normalize_display_name
from app.modules.company_import.normalization import normalize_website_hostname


class CompanyDiscoveryCandidatePromotionError(ValueError):
    pass


class CompanyDiscoveryCandidatePromotionNotFoundError(CompanyDiscoveryCandidatePromotionError):
    pass


class CompanyDiscoveryCandidateNotEligibleError(CompanyDiscoveryCandidatePromotionError):
    pass


class CompanyDiscoveryCandidatePromotionInvalidDataError(CompanyDiscoveryCandidatePromotionError):
    pass


class CompanyDiscoveryCandidatePromotionConsistencyError(CompanyDiscoveryCandidatePromotionError):
    pass


class CandidatePromotionCandidateRecord(Protocol):
    id: int
    project_id: int
    name: str | None
    website: str | None
    country_code: str | None
    candidate_status: CompanyDiscoveryCandidateStatus
    promoted_company_id: int | None


class CandidatePromotionCompanyRecord(Protocol):
    id: int
    project_id: int


class CandidatePromotionStagingRepository(Protocol):
    def get_candidate_for_promotion(
        self,
        project_id: int,
        candidate_id: int,
    ) -> CandidatePromotionCandidateRecord | None: ...

    def link_promoted_company(
        self,
        project_id: int,
        candidate_id: int,
        company_id: int,
    ) -> CandidatePromotionCandidateRecord: ...


class CandidatePromotionCompanyRepository(Protocol):
    def get_for_project(
        self,
        project_id: int,
        company_id: int,
    ) -> CandidatePromotionCompanyRecord | None: ...

    def find_duplicate_by_website(
        self,
        project_id: int,
        website: str,
    ) -> CandidatePromotionCompanyRecord | None: ...

    def create_for_promotion(
        self,
        *,
        project_id: int,
        name: str,
        website: str | None,
        country: str | None,
        status: str = "NEW",
    ) -> CandidatePromotionCompanyRecord: ...


class CompanyDiscoveryCandidatePromotionService:
    def __init__(
        self,
        staging_repository: CandidatePromotionStagingRepository,
        company_repository: CandidatePromotionCompanyRepository,
    ) -> None:
        self.staging_repository = staging_repository
        self.company_repository = company_repository

    def promote(
        self,
        project_id: int,
        candidate_id: int,
    ) -> CompanyDiscoveryCandidatePromotionResult:
        self._validate_positive_id(project_id)
        self._validate_positive_id(candidate_id)

        candidate = self.staging_repository.get_candidate_for_promotion(
            project_id,
            candidate_id,
        )
        if candidate is None:
            raise CompanyDiscoveryCandidatePromotionNotFoundError("Candidate was not found.")
        if candidate.id != candidate_id or candidate.project_id != project_id:
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )

        if candidate.candidate_status == CompanyDiscoveryCandidateStatus.PROMOTED:
            return self._resolve_existing_promotion(candidate, project_id, candidate_id)
        if candidate.candidate_status != CompanyDiscoveryCandidateStatus.REVIEWED:
            raise CompanyDiscoveryCandidateNotEligibleError(
                "Candidate is not eligible for promotion."
            )
        if candidate.promoted_company_id is not None:
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )

        name = self._validate_name(candidate.name)
        website = self._validate_website(candidate.website)
        country = self._validate_country(candidate.country_code)

        company: CandidatePromotionCompanyRecord | None = None
        if website is not None:
            try:
                company = self.company_repository.find_duplicate_by_website(
                    project_id,
                    website,
                )
            except ValueError as error:
                raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                    "Candidate promotion data is invalid."
                ) from error

        created_company = company is None
        if company is None:
            company = self.company_repository.create_for_promotion(
                project_id=project_id,
                name=name,
                website=website,
                country=country,
                status="NEW",
            )
        self._validate_company(company, project_id)

        linked_candidate = self.staging_repository.link_promoted_company(
            project_id,
            candidate_id,
            company.id,
        )
        if (
            linked_candidate.id != candidate_id
            or linked_candidate.project_id != project_id
            or linked_candidate.candidate_status != CompanyDiscoveryCandidateStatus.PROMOTED
            or linked_candidate.promoted_company_id != company.id
        ):
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )

        return CompanyDiscoveryCandidatePromotionResult(
            candidate_id=candidate_id,
            project_id=project_id,
            company_id=company.id,
            previous_status=CompanyDiscoveryCandidateStatus.REVIEWED,
            current_status=CompanyDiscoveryCandidateStatus.PROMOTED,
            created_company=created_company,
            changed=True,
        )

    def _resolve_existing_promotion(
        self,
        candidate: CandidatePromotionCandidateRecord,
        project_id: int,
        candidate_id: int,
    ) -> CompanyDiscoveryCandidatePromotionResult:
        company_id = candidate.promoted_company_id
        if not self._is_positive_id(company_id):
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )
        valid_company_id = cast(int, company_id)
        company = self.company_repository.get_for_project(project_id, valid_company_id)
        if company is None:
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )
        self._validate_company(company, project_id)
        if company.id != valid_company_id:
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )
        return CompanyDiscoveryCandidatePromotionResult(
            candidate_id=candidate_id,
            project_id=project_id,
            company_id=valid_company_id,
            previous_status=CompanyDiscoveryCandidateStatus.PROMOTED,
            current_status=CompanyDiscoveryCandidateStatus.PROMOTED,
            created_company=False,
            changed=False,
        )

    @staticmethod
    def _validate_name(value: str | None) -> str:
        if not isinstance(value, str):
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            )
        try:
            normalized = normalize_display_name(value)
        except ValueError as error:
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            ) from error
        if normalized is None or len(normalized) > 255:
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            )
        return normalized

    @staticmethod
    def _validate_website(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 255:
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            )
        try:
            hostname = normalize_website_hostname(value)
        except ValueError as error:
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            ) from error
        if hostname is None:
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            )
        return value

    @staticmethod
    def _validate_country(value: str | None) -> str | None:
        if value is not None and (not isinstance(value, str) or len(value) > 100):
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            )
        return value

    @classmethod
    def _validate_company(
        cls,
        company: CandidatePromotionCompanyRecord,
        project_id: int,
    ) -> None:
        if not cls._is_positive_id(company.id) or company.project_id != project_id:
            raise CompanyDiscoveryCandidatePromotionConsistencyError(
                "Candidate promotion state is inconsistent."
            )

    @classmethod
    def _validate_positive_id(cls, value: int) -> None:
        if not cls._is_positive_id(value):
            raise CompanyDiscoveryCandidatePromotionInvalidDataError(
                "Candidate promotion data is invalid."
            )

    @staticmethod
    def _is_positive_id(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = [
    "CompanyDiscoveryCandidateNotEligibleError",
    "CompanyDiscoveryCandidatePromotionConsistencyError",
    "CompanyDiscoveryCandidatePromotionError",
    "CompanyDiscoveryCandidatePromotionInvalidDataError",
    "CompanyDiscoveryCandidatePromotionNotFoundError",
    "CompanyDiscoveryCandidatePromotionService",
]

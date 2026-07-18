from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_normalization import normalize_candidate_identity
from app.modules.company_discovery.staging_schemas import (
    CompanyDiscoveryCandidateCreate,
    CompanyDiscoveryCandidateRead,
    CompanyDiscoveryCandidateUpsertResult,
    CompanyDiscoveryRunCreate,
    CompanyDiscoveryRunUpdate,
)
from app.modules.project.models import Project
from app.modules.search_profile.models import SearchProfile


class CompanyDiscoveryStagingNotFoundError(ValueError):
    pass


class CompanyDiscoveryStagingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(self, data: CompanyDiscoveryRunCreate) -> CompanyDiscoveryRun:
        if self.session.get(Project, data.project_id) is None:
            raise CompanyDiscoveryStagingNotFoundError("Project was not found.")
        if data.search_profile_id is not None:
            profile = self.session.get(SearchProfile, data.search_profile_id)
            if profile is None:
                raise CompanyDiscoveryStagingNotFoundError("Search profile was not found.")
            if profile.project_id != data.project_id:
                raise ValueError("Search profile does not belong to the run project.")
        run = CompanyDiscoveryRun(
            project_id=data.project_id,
            search_profile_id=data.search_profile_id,
            provider=data.provider,
            run_status=CompanyDiscoveryRunStatus.PENDING,
            request_fingerprint=data.request_snapshot.fingerprint(),
            request_snapshot=data.request_snapshot.canonical_dict(),
            query_count=data.query_count,
            result_count=data.result_count,
            candidate_count=data.candidate_count,
            error_code=data.error_code,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get_run(self, run_id: int) -> CompanyDiscoveryRun | None:
        return self.session.get(CompanyDiscoveryRun, run_id)

    def list_runs_for_project(
        self,
        project_id: int,
        limit: int,
        offset: int = 0,
        run_status: CompanyDiscoveryRunStatus | None = None,
    ) -> list[CompanyDiscoveryRun]:
        self._validate_pagination(limit, offset)
        statement = select(CompanyDiscoveryRun).where(CompanyDiscoveryRun.project_id == project_id)
        if run_status is not None:
            statement = statement.where(CompanyDiscoveryRun.run_status == run_status)
        return list(
            self.session.scalars(
                statement.order_by(CompanyDiscoveryRun.id).limit(limit).offset(offset)
            )
        )

    def update_run(self, run_id: int, data: CompanyDiscoveryRunUpdate) -> CompanyDiscoveryRun:
        run = self.get_run(run_id)
        if run is None:
            raise CompanyDiscoveryStagingNotFoundError("Discovery run was not found.")
        for field in data.model_fields_set:
            setattr(run, field, getattr(data, field))
        self.session.add(run)
        self.session.flush()
        return run

    def get_candidate(self, candidate_id: int) -> CompanyDiscoveryCandidate | None:
        return self.session.get(CompanyDiscoveryCandidate, candidate_id)

    def list_candidates_for_project(
        self,
        project_id: int,
        limit: int,
        offset: int = 0,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> list[CompanyDiscoveryCandidate]:
        self._validate_pagination(limit, offset)
        statement = select(CompanyDiscoveryCandidate).where(
            CompanyDiscoveryCandidate.project_id == project_id
        )
        if candidate_status is not None:
            statement = statement.where(
                CompanyDiscoveryCandidate.candidate_status == candidate_status
            )
        return list(
            self.session.scalars(
                statement.order_by(CompanyDiscoveryCandidate.id).limit(limit).offset(offset)
            )
        )

    def upsert_candidate(
        self,
        project_id: int,
        run_id: int,
        data: CompanyDiscoveryCandidateCreate,
    ) -> CompanyDiscoveryCandidateUpsertResult:
        if data.project_id != project_id:
            raise ValueError("Candidate project ID does not match repository scope.")
        if data.run_id != run_id:
            raise ValueError("Candidate run ID does not match repository scope.")
        run = self.get_run(run_id)
        if run is None:
            raise CompanyDiscoveryStagingNotFoundError("Discovery run was not found.")
        if run.project_id != project_id:
            raise ValueError("Discovery run does not belong to the candidate project.")

        normalized = normalize_candidate_identity(
            name=data.name, website=data.website, country_code=data.country_code
        )
        existing = self.session.scalar(
            select(CompanyDiscoveryCandidate).where(
                CompanyDiscoveryCandidate.project_id == project_id,
                CompanyDiscoveryCandidate.identity_key == normalized.identity_key,
            )
        )
        if existing is None:
            created = CompanyDiscoveryCandidate(
                project_id=project_id,
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
                provider=data.provider,
                name=normalized.name,
                normalized_name=normalized.normalized_name,
                website=normalized.website,
                website_identity=normalized.website_identity,
                country_code=normalized.country_code,
                identity_key=normalized.identity_key,
                best_position=data.position,
                candidate_status=CompanyDiscoveryCandidateStatus.DISCOVERED,
                promoted_company_id=None,
            )
            self.session.add(created)
            self.session.flush()
            return self._result(created, created=True)

        changed = False
        if existing.last_seen_run_id != run_id:
            existing.last_seen_run_id = run_id
            changed = True
        if data.position is not None and (
            existing.best_position is None or data.position < existing.best_position
        ):
            existing.best_position = data.position
            changed = True

        protected = existing.candidate_status != CompanyDiscoveryCandidateStatus.DISCOVERED
        if not protected:
            incoming = {
                "name": normalized.name,
                "normalized_name": normalized.normalized_name,
                "website": normalized.website,
                "website_identity": normalized.website_identity,
                "country_code": normalized.country_code,
            }
            for field, value in incoming.items():
                if getattr(existing, field) is None and value is not None:
                    setattr(existing, field, value)
                    changed = True
        if changed:
            self.session.add(existing)
            self.session.flush()
        return self._result(existing, updated=changed, protected=protected)

    @staticmethod
    def _result(
        candidate: CompanyDiscoveryCandidate,
        *,
        created: bool = False,
        updated: bool = False,
        protected: bool = False,
    ) -> CompanyDiscoveryCandidateUpsertResult:
        return CompanyDiscoveryCandidateUpsertResult(
            candidate=CompanyDiscoveryCandidateRead.model_validate(candidate),
            created=created,
            updated=updated,
            protected=protected,
        )

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("Limit must be greater than zero.")
        if isinstance(offset, bool) or offset < 0:
            raise ValueError("Offset must not be negative.")


__all__ = [
    "CompanyDiscoveryStagingNotFoundError",
    "CompanyDiscoveryStagingRepository",
]
